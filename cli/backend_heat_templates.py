import sys
import os
from os import environ as env
import pnda_cli_utils as utils
import shutil
import glob
import jinja2
import yaml
from backend_base import BaseBackend
import heatclient
from heatclient import client as heat_client
from keystoneauth1.identity import v2
from keystoneauth1.identity import v3
from keystoneauth1 import session
from heatclient import exc
from heatclient.common import template_utils

utils.init_logging()
CONSOLE = utils.CONSOLE_LOGGER
LOG = utils.FILE_LOGGER
LOG_FILE_NAME = utils.LOG_FILE_NAME

class HeatTemplateBackend(BaseBackend):
    '''
    Deployment specific implementation for OpenStack Heat Templates
    '''
    def __init__(self, pnda_env, cluster, no_config_check, flavor, keyname, branch, dry_run):
        self._dry_run = dry_run
        super(HeatTemplateBackend, self).__init__(
            pnda_env, cluster, no_config_check, flavor, '%s.pem' % keyname, branch)

    def _get_keystone_session(self):
        username=self._pnda_env['openstack_parameters']['KEYSTONE_USER']
        password=self._pnda_env['openstack_parameters']['KEYSTONE_PASSWORD']
        auth_url=self._pnda_env['openstack_parameters']['KEYSTONE_AUTH_URL']
        if self._pnda_env['openstack_parameters']['KEYSTONE_AUTH_VERSION'] == 2:
            print "Inside v2 check:"
            tenant_name=self._pnda_env['openstack_parameters']['KEYSTONE_TENANT']
            auth=v2.Password(auth_url=auth_url,username=username, password=password, tenant_name=tenant_name)
        elif self._pnda_env['openstack_parameters']['KEYSTONE_AUTH_VERSION'] == 3:
            project_name=self._pnda_env['openstack_parameters']['KEYSTONE_TENANT']
            #user_domain_name=self._pnda_env['openstack_parameters']['KEYSTONE_USER']
            #project_domain_name=self._pnda_env['openstack_parameters']['KEYSTONE_USER']
            auth=v3.Password(auth_url=auth_url,username=username, password=password, project_name=project_name)
        else:
            print "Invalid Auth API version"
        keystone_session = session.Session(auth=auth)
        return keystone_session,auth

    def check_target_specific_config(self):
        '''
        Check Openstack specific configuration has been entered correctly
        '''
        self._check_openstack_connection()
        name = self._keyname_from_keyfile(self._keyfile)
        self._check_keypair(name)

    def _check_openstack_connection(self):
        pass

    def _keyname_from_keyfile(self, keyfile):
        return keyfile[:-4]

    def _check_keypair(self, keyname):
        pass

    def load_node_config(self):
        pass

    def fill_instance_map(self):
        pass

    def pre_install_pnda(self, node_counts):
        '''
        Use the Openstack heatclient API to launch a stack that PNDA can be installed on
        The stack is defined in template files in the flavor specific heat-template directory
        '''
        #Generate template files
        template_data = self._generate_template_file(
            self._flavor, node_counts['datanodes'], node_counts['opentsdb_nodes'], node_counts['kafka_nodes'], node_counts['zk_nodes'])
#            self._es_counts['elk_es_master'], self._es_counts['elk_es_ingest'], self._es_counts['elk_es_data'],
#            self._es_counts['elk_es_coordinator'], self._es_counts['elk_es_multi'], self._es_counts['elk_logstash']) 
        
        stack_params=[]
        print "zk_nodes: " , node_counts['zk_nodes']
        print "kafka_nodes: ", node_counts['kafka_nodes']
        print "datanodes: ", node_counts['datanodes']
        print "opentsdb_nodes: ", node_counts['opentsdb_nodes']
        stack_params.append('ZookeeperNodes={}'.format(node_counts['zk_nodes']))
        stack_params.append('KafkaNodes={}'.format(node_counts['kafka_nodes']))
        stack_params.append('DataNodes={}'.format(node_counts['datanodes']))
        stack_params.append('OpentsdbNodes={}'.format(node_counts['opentsdb_nodes']))
        stack_params.append('PndaFlavor={}'.format(self._flavor))
        stack_params.append('KeyName={}'.format(self._keyfile[:-4]))
        stack_params_string = ';'.join(stack_params)
        print "stack_params_string is: "
        print stack_params_string
        print "stack_param: ", stack_params
        self._deploy_stack(stack_params_string)

    def _merge_dicts(self, base, mergein):
        for element in mergein:
            if element not in base or not base[element]:
                base[element] = mergein[element]
            else:
                for child in mergein[element]:
                     # base has priority over mergein, so don't overwrite base elements
                    if child not in base[element]:
                        base[element][child] = mergein[element][child]

    def _generate_template_file(self, flavor, datanodes, opentsdbs, kafkas, zookeepers):
        resources_dir = '_resources_{}-{}'.format(flavor, self._cluster)
        dest_dir = '{}/{}'.format(os.getcwd()+'/cli', resources_dir)
        if os.path.isdir(dest_dir):
            shutil.rmtree(dest_dir)
        os.makedirs(dest_dir)

        exclude_sections = ['aws_parameters', 'existing_machines_parameters']
        with open( dest_dir + '/pnda_env_openstack.yaml', 'w') as pnda_env_openstack:
            pnda_env_openstack.write('parameter_defaults:\n')
            for section in self._pnda_env:
                if section not in exclude_sections:
                    for setting in self._pnda_env[section]:
                        val = '"%s"' % self._pnda_env[section][setting] if isinstance(self._pnda_env[section][setting], (list, tuple)) else self._pnda_env[section][setting]
                        pnda_env_openstack.write('  %s: %s\n' % (setting, val))
        pnda_env_openstack.close()

        for yaml_file in glob.glob('heat-templates/%s/*.yaml' % flavor):
            shutil.copy(yaml_file, dest_dir)
        self._generate_instance_templates(os.path.abspath('heat-templates/%s' % flavor),
                                   os.path.abspath(dest_dir))

        with open( dest_dir + '/pnda_env_openstack.yaml', 'r') as pnda_env_openstack:
            pnda_env= yaml.load(pnda_env_openstack)
        with open(dest_dir+'/resource_registry.yaml', 'r') as infile:
            resource_registry = yaml.load(infile)
        with open(dest_dir+'/instance_flavors.yaml', 'r') as infile:
            instance_flavors = yaml.load(infile)
        self._merge_dicts(pnda_env, instance_flavors)
        self._merge_dicts(pnda_env, resource_registry)
        print pnda_env
        with open(dest_dir + '/pnda_env.yaml', 'w') as outfile:
            yaml.dump(pnda_env, outfile, default_flow_style=False)

    def _generate_instance_templates(self, from_dir, to_dir):
        template_env = jinja2.Environment(loader=jinja2.FileSystemLoader(searchpath='/'))
        print from_dir
        print to_dir
        for j2_file in glob.glob('%s/*.j2' % from_dir):
            print 'processing template file: %s' % j2_file
            template = template_env.get_template(j2_file)
            yaml_file_content = yaml.load(template.render())
            #print yaml_file_content
            yaml_file = '{}/{}'.format(to_dir, os.path.basename(j2_file[:-3]))
            with open(yaml_file, 'w') as outfile:
                yaml.dump(yaml_file_content, outfile, default_flow_style=False)

        with open('%s/pnda.yaml' % to_dir, 'r') as infile:
            pnda_flavor = yaml.load(infile)
        template = template_env.get_template(os.path.abspath('./heat-templates/pnda.yaml'))
        pnda_common = yaml.load(template.render())
        self._merge_dicts(pnda_common, pnda_flavor)
        with open('%s/pnda.yaml' % to_dir, 'w') as outfile:
            yaml.dump(pnda_common, outfile, default_flow_style=False)

    def _deploy_stack(self,stack_params):
        stack_name = self._cluster
        # Get Environment variables
        auth_url=os.getenv('OS_AUTH_URL')
        
        # Keystone authentication
        keystone_session,keystone_auth=  self._get_keystone_session()
        kwargs = {
        'auth_url': auth_url,
        'session': keystone_session,
        'auth': keystone_auth,
        'service_type': 'orchestration'}

        heat_session = heat_client.Client(version='1', **kwargs)
        templates_path=os.getcwd() + '/cli/' + '_resources_{}-{}'.format(self._flavor,self._cluster)
        template_file=templates_path + "/pnda.yaml"
        print "Template file is : " + template_file
        env_file=templates_path + "/pnda_env.yaml"
        env_param=[env_file]
        print "Env file is : " + env_file
        tpl_files, tpl_template = template_utils.process_template_path(template_file)
        e_files, e_template = template_utils.process_multiple_environments_and_files(env_paths=env_param)
        files_all=files=dict(list(tpl_files.items()) + list(e_files.items()))

        print "heat_session is: "
        print(list(heat_session.stacks.list()))

        print "parameters received is "
        print stack_params
        try:
            status=heat_session.stacks.create(stack_name=stack_name, template=tpl_template ,files=files_all,environment=e_template, timeout_mins=120, parameter=stack_params)
            print status
        except heatclient.exc.HTTPConflict as e:
            error_state = e.error
            print("Stack already exists : " , error_state , stack_name)

        except heatclient.exc.HTTPBadRequest as e:
            error_state = e.error
            print("Bad request : ", error_state)
