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
  stages {
    stage("setup"){
      steps{
        script{
          sh '''
          python3 -m venv ${env.WORKSPACE}/env
          pip install -U setuptools pip ${get_pip_options()}
          pip install -U requirements.dev.txt ${get_pip_options()}
          pip install -U -c requirements.txt . ${get_pip_options()}
          '''
        }
      }
    }
    stage("code linting"){
      steps{
        script{
          sh'''
          ${WORKSPACE}/env/bin/flake8 *.py src tests
          '''
        }
      }
    }
    stage("tests"){
      steps{
        script{
          sh'''
          ${WORKSPACE}/env/bin/pytest" tests -v -s --junitxml=junit.xml --cov=inmanta_plugins.yang  --cov-report term --cov-report xml:coverage.xml
          '''
          junit 'junit.xml'
          cobertura coberturaReportFile: 'coverage.xml'
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
        rm -rf ${env.WORKSPACE}/env
        echo "Cleanup finished"
        '''
      }
    }
  }
}
