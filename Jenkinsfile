def get_pip_options() {
  if(env.pytest_inmanta_dev){
    return "--pre -i https://artifacts.internal.inmanta.com/inmanta/dev"
  }
  return ""
}

pipeline {
  agent any
  triggers {
    cron(BRANCH_NAME == "master" ? "H H(2-5) * * *": "")
  }
  options {
    disableConcurrentBuilds()
  }
  parameters {
    booleanParam(name:"pytest_inmanta_dev" ,defaultValue: true, description: 'Changes the index used to install pytest-inmanta to the inmanta dev index')
  }
  environment{
     INMANTA_LSM_HOST="192.168.2.102"
  }
  stages {
    stage("setup"){
      steps{
        script{
          sh """
          python3 -m venv ${env.WORKSPACE}/env
          ${env.WORKSPACE}/env/bin/pip install -U setuptools pip ${get_pip_options()}
          ${env.WORKSPACE}/env/bin/pip install -U -r requirements.dev.txt ${get_pip_options()}
          ${env.WORKSPACE}/env/bin/pip install -U -c requirements.txt . ${get_pip_options()}
          """
        }
      }
    }
    stage("code linting"){
      steps{
        script{
          sh'''
          ${WORKSPACE}/env/bin/flake8 src tests examples *.py
          '''
        }
      }
    }
    stage("tests"){
      steps{
        sshagent(credentials : ['96f313c8-b5db-4978-ac85-d314ac372b8f']) {
          withCredentials([string(credentialsId: 'fff7ef7e-cb20-4fb2-a93b-c5139463c6bf', variable: 'GITHUB_TOKEN')]) {
            script{
              sh"""
              INMANTA_MODULE_REPO="https://${GITHUB_TOKEN}@github.com/inmanta/{}.git" ${env.WORKSPACE}/env/bin/pytest tests -v -s --junitxml=junit.xml
              """
              junit 'junit.xml'
            }
          }
        }
      }
    }
    stage("release") {
      steps {
        withCredentials([usernamePassword(credentialsId: 'devpi-user', usernameVariable: 'DEVPI_USER', passwordVariable: 'DEVPI_PASS')]) {
          sh'''
            "/opt/devpi-client/venv/bin/devpi" use https://artifacts.internal.inmanta.com/inmanta/dev/

            "/opt/devpi-client/venv/bin/devpi" login "${DEVPI_USER}" --password="${DEVPI_PASS}"

            rm -f dist/*

            if [ "${BRANCH_NAME}" = "master" ];
            then
              "${WORKSPACE}/env/bin/python3" setup.py egg_info sdist
            else
              "${WORKSPACE}/env/bin/python3" setup.py egg_info -Db ".dev$(date +'%Y%m%d%H%M%S' --utc)" sdist
            fi

            "/opt/devpi-client/venv/bin/devpi" upload dist/*

            "/opt/devpi-client/venv/bin/devpi" logoff
          '''
        }
      }
    }
  }
  post {
    always {
      script {
        sh'''
        echo "Cleanup"
        rm -rf ${WORKSPACE}/env
        echo "Cleanup finished"
        '''
      }
    }
  }
}
