import sys
import os
import time
import docker
import tarfile
import re

if len(sys.argv) == 3:
    splunk_docker_repo = sys.argv[1]
    cypress_docker_repo = sys.argv[2]
else:
    print("USAGE: python run_tests.py <splunk_docker_repo> <cypress_docker_repo>")
    sys.exit()


# Assits with moving local files to container
# Ex. copy_to('/local/foo.txt', 'my-container:/tmp/foo.txt')
def copy_to(src, dst):
    name, dst = dst.split(':')
    container = client.containers.get(name)

    os.chdir(os.path.dirname(src))
    srcname = os.path.basename(src)
    tar = tarfile.open(src + '.tar', mode='w')
    try:
        tar.add(srcname)
    finally:
        tar.close()

    data = open(src + '.tar', 'rb').read()
    container.put_archive(os.path.dirname(dst), data)

    os.remove(src + '.tar')

def copy_from(dst, src):
    name, dst = dst.split(':')
    container = client.containers.get(name)

    raw_data, stats = container.get_archive(dst)

    file = open(src+'.tar', 'wb')
    for chunk in raw_data:
        file.write(chunk)
    file.close()

    tar = tarfile.open(src+'.tar', "r:")
    tar.extractall()
    tar.close()

    # tar = tarfile.open(src, 'w|')
    # tar.add(raw_data)
    # tar.close()

    # tar = tarfile.open(src + '.tar', mode='w|')

    # os.chdir(os.path.dirname(src))
    # srcname = os.path.basename(src)
    # tar.addfile(raw_data)
    # try:
    #     tar.add(srcname)
    # finally:
    #     tar.close()
    #
    # data = open(src + '.tar', 'rb').read()
    # container.put_archive(os.path.dirname(dst), data)

    # os.remove(src + '.tar')


MAX_WAIT_SECONDS = 120

splunk_container_name = "splunk"
app_root = "testing_app"

SPLUNK_APPS_DIR = "/opt/splunk/etc/apps"
SPLUNK_HOME = "/opt/splunk"
SPLUNK_ETC = "/opt/splunk/etc"
SPLUNK_START_ARGS = "--accept-license"
SPLUNK_ENABLE_LISTEN = "9997"
SPLUNK_ADD = "tcp 1514"
SPLUNK_USER = "admin"
SPLUNK_PASSWORD = "newPassword"
SPLUNK_HOSTNAME = "idx-example.splunkcloud.com"

CYPRESS_baseUrl = "http://" + splunk_container_name + ":8000"
CYPRESS_base_api = "https://" + splunk_container_name + ":8089"
CYPRESS_headless = "true"

CYPRESS_ENV = {'CYPRESS_baseUrl': CYPRESS_baseUrl, 'CYPRESS_base_api': CYPRESS_base_api,
               'CYPRESS_headless': CYPRESS_headless}

SPLUNK_ENV = {'SPLUNK_HOME': SPLUNK_HOME, 'SPLUNK_ETC': SPLUNK_ETC, 'SPLUNK_START_ARGS': SPLUNK_START_ARGS,
              'SPLUNK_ENABLE_LISTEN': SPLUNK_ENABLE_LISTEN, 'SPLUNK_ADD': SPLUNK_ADD,
              'SPLUNK_PASSWORD': SPLUNK_PASSWORD}

docker_network_name = "testingnet"
docker_user = 'root'

project_dir = os.getcwd()

if not os.path.exists('build'):
    os.makedirs('build')

print("Starting Docker Client")
client = docker.from_env()

print("Cleaning up any running Splunk/Cypress containers and testing networks left over from previous tests")
client.volumes.prune()
for container in client.containers.list(all=True, filters={"name": "cypress_runner"}):
    print(container)
    try:
        container.kill()
    except:
        pass
    try:
        container.remove()
    except:
        pass
for container in client.containers.list(all=True, filters={"name": splunk_container_name}):
    print(container)
    try:
        container.kill()
    except:
        pass
    try:
        container.remove()
    except:
        pass

for network in client.networks.list(filters={"name": docker_network_name}):
    print(network)
    try:
        network.remove()
    except:
        pass

print("Running Image: {}\n".format(splunk_docker_repo))

print("Creating a bridge network for the containers to communicate\n")

docker_network = client.networks.create(docker_network_name)

print("Docker Network {} created\n".format(docker_network_name))

print("Starting Splunk Image {} as {}\n".format(splunk_docker_repo, splunk_container_name))

splunk_container = client.containers.run(detach=True, image=splunk_docker_repo, name=splunk_container_name,
                                         network=docker_network_name,
                                         hostname=SPLUNK_HOSTNAME, environment=SPLUNK_ENV, user=docker_user,
                                         ports={'8000/tcp': 8000, '8089/tcp': 8089})

print("Creating directory: /opt/splunk/etc/apps\n")

splunk_container.exec_run("mkdir -p -m 777 /opt/splunk/etc/apps")

print("Copying files to Splunk Container\n")
copy_to(project_dir + '/cicd/config/passwd', splunk_container_name + ':' + SPLUNK_ETC + "/passwd")
copy_to(project_dir + '/' + app_root, splunk_container_name + ':' + SPLUNK_APPS_DIR + "/" + app_root)
copy_to(project_dir + '/output', splunk_container_name + ':/')

print("Touching ui_login file\n")
splunk_container.exec_run("touch /opt/splunk/etc/.ui_login")

while splunk_container.status != 'running':
    print("Waiting for Splunk container to be ready.\n")
    time.sleep(2)
    splunk_container.reload()

    # Guarantee Splunk is fully loaded
    time.sleep(15)

print('Splunk is Running. Now testing to see if data is loaded.\n')

# Cleaning out dispatch so that we don't run out of space
splunk_container.exec_run("rm -rf /opt/splunk/var/run/splunk/dispatch")

splunk_ready = False
while not splunk_ready:
    (exit_code, output) = splunk_container.exec_run(
        "bash -c " + "\"SPLUNK_USERNAME=admin SPLUNK_PASSWORD=newPassword /opt/splunk/bin/splunk search \'index=main source=/output/access.log | stats count\' -app testing_app\"")
    event_count = re.search('\d+', str(output.strip()))

    if event_count and event_count.group() == '1559':
        splunk_ready = True

    time.sleep(5)


print('Data is Loaded and ready.\n')

print('Setting up Test Environment\n')
print("Running btool checks...\n")

(exit_code, output) = splunk_container.exec_run('bash -c "/opt/splunk/bin/splunk btool check --debug"')
btool_logging = open(project_dir + '/output/btool_output.txt', 'wb')
btool_logging.writelines(output.splitlines(True))

print('Executing Cypress Test Specs')
cypress_container = client.containers.create(name='cypress_runner', network=docker_network_name, working_dir='/e2e',
                                             environment=CYPRESS_ENV, image=cypress_docker_repo
                                             )

# copy in configuration and tests
copy_to(project_dir+'/cicd/test/cypress', 'cypress_runner:/e2e/cypress')
copy_to(project_dir+'/cicd/test/cypress.json', 'cypress_runner:/e2e/cypress.json')

time.sleep(30)

print(cypress_container.start())

print(docker_network.containers)

print(cypress_container.logs().decode('utf-8'))


try:
    copy_from('cypress_runner:/e2e/cypress/videos', project_dir+'/cicd/test/cypress/videos')
except Exception as e:
    print("Couldn't Copy Cypress videos")
    print(e)


print("KILLING SPLUNK CONTAINER and DOCKER NETWORK")
splunk_container.kill()
splunk_container.remove()
cypress_container.kill()
cypress_container.remove()
docker_network.remove()
client.volumes.prune()
