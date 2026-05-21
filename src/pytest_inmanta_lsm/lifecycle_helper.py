"""
:copyright: 2026 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""
import os
import copy
import pathlib
import typing
from collections import defaultdict
from dataclasses import dataclass

import inmanta_lsm.model
from inmanta_lsm.model import AttributeOperation
from inmanta_plugins.lsm import (  # type: ignore
    Lifecycle,
    LifecycleState,
    LifecycleTransfer,
)
from pytest_inmanta_lsm import lsm_project
from pytest_inmanta_lsm.lsm_project import (
    perform_attribute_operation,
    resource_attributes_hash,
)

import inmanta


class LifecycleHelper:
    def __init__(
        self,
        lsm_project: lsm_project.LsmProject,
        service: inmanta_lsm.model.ServiceInstance,
        render: bool = False,
        full_compile: bool = False,
    ) -> None:
        self.lsm_project = lsm_project
        self.render = render
        self.service = service

        self.lifecycle: Lifecycle = lsm_project.get_service_entity(
            service.service_entity
        ).lifecycle

        self.same_desired_state_transfers: list[tuple[str, str]] = []
        self.same_desired_state_transfers_candidates: list[tuple[str, str]] = []
        self.unexpected_diff: dict[tuple[str, str], LifecycleHelper.ResourceDiff] = {}
        self.full_compile = full_compile

    @dataclass
    class AttributeUpdate:
        source: str
        target: str
        operation: typing.Literal["set", "add", "remove"]
        path: str
        element: object

    @dataclass
    class ResourceDiff:
        lost: set
        added: set
        modified: set

    def prunable_transfers(
        self,
        state: str,
        visited: set[str],
    ) -> tuple[list[tuple[str, str]], int]:
        """
        Return all unecessary transfers for the visiting lifecycle function (`prunable transfers`).
        The only condition for a transfer to be pruned is that the transfer itself
        is not state preserving and it does not lead to a state preserving transfer using depth first search.

        :param state: The state we are currently visiting.
        :param visited: The set of names of all the states we already visited.
        """
        visited.add(state)
        all_transfers_to_prune = []
        n_pruned = 0
        for next_state in self.outgoing_nodes[state]:
            all_transfers_to_prune_below, n_pruned_below = [], 0  # type: ignore
            next_already_visited = next_state.name in visited
            if not next_already_visited:
                all_transfers_to_prune_below, n_pruned_below = self.prunable_transfers(
                    next_state.name, visited
                )

            transfer = self.transfers[(state, next_state.name)]

            is_next_state_error = next_state.name == transfer.error
            should_have_same_desired_state = (
                transfer.error_same_desired_state
                if is_next_state_error
                else transfer.target_same_desired_state
            )

            prune_below = len(self.outgoing_nodes[next_state.name]) == n_pruned_below

            if not should_have_same_desired_state and (
                prune_below or next_already_visited
            ):
                # Prune this transfer if the transfer itself is not considered as state preserving
                # and if it doesn't lead to any state preserving transfer we haven't visited via another branch.
                all_transfers_to_prune.append((state, next_state.name))
                n_pruned += 1

            all_transfers_to_prune.extend(all_transfers_to_prune_below)

        return all_transfers_to_prune, n_pruned

    def get_prunable_transfers(self) -> list[tuple[str, str]]:
        """
        Return all unecessary transfers for the visiting lifecycle function (`prunable transfers`).
        The only condition for a transfer to be pruned is that the transfer itself
        is not state preserving and it does not lead to a state preserving transfer using depth first search.
        """
        transfers_to_prune, _ = self.prunable_transfers(
            self.lifecycle.get_state(self.lifecycle.initial_state).name, set()
        )
        return transfers_to_prune

    def set_transfers_to_visit(
        self, states: list[str] | None = None, exploration: bool = False
    ) -> None:
        """
        Initialize all transfers to visit with the given list of states.
        If no states are provided, then the default is to take into account all states of the lifecycle.
        If exploration boolean is set, no transfers will be pruned.
        """

        def set_loop_same_state(
            transfer: LifecycleTransfer, error: bool = False
        ) -> bool:
            """
            Prune self loop transfer and already insert them in candidates if transfer boolean not already set.
            Return true if the transfer is a loop.
            """
            target = transfer.error if error else transfer.target
            if transfer.source == target:
                self.same_desired_state_transfers.append((transfer.source, target))
                if (not error and not transfer.target_same_desired_state) or (
                    error and not transfer.error_same_desired_state
                ):
                    self.same_desired_state_transfers_candidates.append(
                        (transfer.source, target)
                    )
                return True
            return False

        # Dictionary of states, used to easily find next states base on current state.
        self.outgoing_nodes: dict[str, list[LifecycleState]] = defaultdict(list)
        # Dictionary of transfers with key being tuple (source, target).
        self.transfers: dict[tuple[str, str], LifecycleTransfer] = dict()
        for transfer in self.lifecycle.transfers:
            visit_target = (
                True
                if not states
                else transfer.source in states and transfer.target in states
            )
            visit_error = (
                transfer.error is not None
                if not states
                else transfer.source in states and transfer.error in states
            )

            targets = []
            if visit_target:
                self_loop = set_loop_same_state(transfer, error=False)
                if not self_loop:
                    targets.append(self.lifecycle.get_state(transfer.target))
                    self.transfers[(transfer.source, transfer.target)] = transfer
            if visit_error:
                self_loop = set_loop_same_state(transfer, error=True)
                if not self_loop:
                    targets.append(self.lifecycle.get_state(transfer.error))
                    self.transfers[(transfer.source, transfer.error)] = transfer

            self.outgoing_nodes[transfer.source].extend(targets)

        # Prune transitions that are not needed for the test
        transfers_to_prune = [] if exploration else self.get_prunable_transfers()

        for source, target in transfers_to_prune:
            self.outgoing_nodes[source] = [
                state for state in self.outgoing_nodes[source] if state.name != target
            ]
            del self.transfers[(source, target)]

        self.states: set[str] = set()
        for source, target in self.transfers:
            self.states.add(source)
            self.states.add(target)

    def get_current_hashed_resources(self) -> dict:
        return {
            resource_id: resource_attributes_hash(resource)
            for resource_id, resource in self.lsm_project.project.resources.items()
        }

    def get_resource_diff(self, resources_a: dict, resources_b: dict) -> dict:
        lost = resources_a.keys() - resources_b.keys()
        added = resources_b.keys() - resources_a.keys()

        common_resources = set(resources_a.keys()).intersection(resources_b.keys())
        modified = {
            key for key in common_resources if resources_a[key] != resources_b[key]
        }

        return LifecycleHelper.ResourceDiff(  # type: ignore
            lost=set(lost), added=set(added), modified=modified
        )

    # Function to draw the graph and save it to a file
    def draw_graph_to_file(
        self,
        visited: set[str | tuple[str, str]],
        current_transfer: tuple[str, str] | None = None,
        filename="graph_state",
    ):
        # This requires installing graphiz on the system (not only Python)
        # https://pypi.org/project/graphviz/
        import graphviz  # type: ignore

        # Create a directed graph
        dot = graphviz.Digraph(
            name="lifecycle",
            filename=filename,
            directory=pathlib.Path(os.environ.get("PWD", os.getcwd())) / "tests" / "graph",
            format="png",
        )

        # create nodes
        for node in self.states:
            if current_transfer and node == current_transfer[1]:
                # Current node in cyan
                dot.node(name=node, color="#00b5ff")
            elif node in visited:
                # Visited nodes in dark blue
                dot.node(name=node, color="#477d93")
            else:
                # Unvisited nodes in light gray
                dot.node(name=node, color="lightgray")

        for edge in self.transfers:
            target_operation = self.transfers[edge].target_operation
            label = str(target_operation) if target_operation else None

            if current_transfer == edge and edge not in visited:
                # Current edge in cyan
                dot.edge(edge[0], edge[1], color="#00b5ff", label=label)
            elif edge in visited:
                if edge in self.unexpected_diff:
                    # failed transfer in red
                    dot.edge(edge[0], edge[1], color="red", label=label)
                else:
                    # Successful transfer in green
                    dot.edge(edge[0], edge[1], color="green", label=label)
            else:
                # Unvisited transfer in black
                dot.edge(edge[0], edge[1], color="black", label=label)

        dot.render()

    def visit_lifecycle(
        self,
        updates: dict[tuple[str, str], AttributeUpdate],
        state: str,
        visited: set[str | tuple[str, str]],
    ) -> None:
        """
        Recursively visit lifecycle using depth first search. It executes a compile for each transfer and
        verify resource difference for transfer who claim to be state preserving.
        While traversing the list of transfers, `same_desired_state_transfers` list is filled with transfers
        that did not produce a resource diff.

        :param updates: Dictionary of updates to perform during certain transfers, the key is tuple (source, target).
        :param state: The state we are currently visiting.
        :param visited: The set of names of all the states we already visited.
        """
        visited.add(state)
        # save desired state
        current_services = copy.deepcopy(self.lsm_project.services)
        current_resources = self.get_current_hashed_resources()

        for next_state in self.outgoing_nodes[state]:
            transfer = self.transfers[(state, next_state.name)]

            if self.render:
                self.draw_graph_to_file(
                    visited, current_transfer=(state, next_state.name)
                )

            # update candidates attributes if there is an update for this transfer
            update: LifecycleHelper.AttributeUpdate = updates.get(
                (state, next_state.name), None  # type: ignore
            )
            if update:
                self.service.candidate_attributes = copy.deepcopy(
                    self.service.active_attributes
                )
                attribute = inmanta.util.dict_path.to_path(update.path).get_element(
                    self.service.candidate_attributes
                )

                match update.operation:
                    case "add":
                        if type(attribute) is not list:
                            raise RuntimeError(
                                f"AttributeUpdate operation `add` expects a list, given: {type(attribute)} !"
                            )
                        attribute.append(update.element)
                    case "remove":
                        if type(attribute) is not list:
                            raise RuntimeError(
                                f"AttributeUpdate operation `remove` expects a list, given: {type(attribute)} !"
                            )
                        attribute = [
                            element
                            for element in attribute
                            if not (update.element.items() < element.items())  # type: ignore
                        ]
                    case _:
                        attribute = update.element

                inmanta.util.dict_path.to_path(update.path).set_element(
                    self.service.candidate_attributes, attribute
                )

            # Make the validation compile if this transfer requires it
            is_next_state_error = next_state.name == transfer.error
            validation = transfer.validate_ and not is_next_state_error
            if validation:
                self.lsm_project.compile(service_id=self.service.id, validation=True)

            self.service.state = next_state.name

            # Execute the target operation if any
            target_operation = (
                transfer.error_operation
                if is_next_state_error
                else transfer.target_operation
            )
            if target_operation:
                perform_attribute_operation(
                    self.service, AttributeOperation(target_operation)
                )

            # Compile and make sure transfer is state preserving if it claims to be.
            should_have_same_desired_state = (
                transfer.error_same_desired_state
                if is_next_state_error
                else transfer.target_same_desired_state
            )

            # exporting compile
            if self.full_compile:
                self.lsm_project.compile(validation=False)
            else:
                self.lsm_project.compile(service_id=self.service.id, validation=False)

            new_resources = self.get_current_hashed_resources()
            no_resource_diff = current_resources == new_resources
            if should_have_same_desired_state and not no_resource_diff:
                # Compile should be state preserving, store the unexpected resource diff to report it later.
                self.unexpected_diff[(state, next_state.name)] = self.get_resource_diff(  # type: ignore
                    current_resources, new_resources
                )

            if no_resource_diff:
                self.same_desired_state_transfers.append(
                    (transfer.source, transfer.target)
                )
            if no_resource_diff and not should_have_same_desired_state:
                self.same_desired_state_transfers_candidates.append(
                    (transfer.source, transfer.target)
                )

            visited.add((state, next_state.name))
            if next_state.name not in visited:
                self.visit_lifecycle(
                    updates,
                    next_state.name,
                    visited,
                )
            # make sure to restore the services
            self.lsm_project.services = copy.deepcopy(current_services)

    def verify_lifecycle_correctness(
        self,
        updates: dict[tuple[str, str], AttributeUpdate],
        states_to_visit: str | None = None,
        exploration: bool = False,
    ) -> None:
        """
        Traverse the lifecycle and gather useful informations :
            - `same_desired_state_transfers` will contain all transfers that did not produce a resource diff.
            - `same_desired_state_transfers_candidates` will contain all transfers candidates that did not produce \
                a resource diff but do not have `target_same_desired_state` or `target_same_desired_state` set.
            - `unexpected_diff` will contain all unexpected diff in resources for each transfer that
                claim to be state preserving.
        If exploration boolean is set, no transfer pruning is executed.
        """
        # Reset explored data
        self.same_desired_state_transfers = []
        self.same_desired_state_transfers_candidates = []
        self.unexpected_diff = {}

        self.set_transfers_to_visit(states=states_to_visit, exploration=exploration)  # type: ignore

        visited = set()  # type: ignore
        self.visit_lifecycle(
            updates=updates,
            state=self.service.state,
            visited=visited,
        )
        if self.render:
            # make sure last node is colored
            self.draw_graph_to_file(visited)
