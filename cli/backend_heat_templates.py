import sys
import os
import shutil
import glob
import jinja2
import yaml
import time
import json
import ruamel.yaml
from ruamel.yaml.util import load_yaml_guess_indent
import heatclient
from heatclient import client as heat_client
from keystoneauth1.identity import v2
from keystoneauth1.identity import v3
from keystoneauth1 import session
from heatclient import exc
from heatclient.common import template_utils
from backend_base import BaseBackend
import pnda_cli_utils as utils
from novaclient import client as nova_client

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
        if int(self._pnda_env['openstack_parameters']['KEYSTONE_AUTH_VERSION']) == 2:
            print "Inside v2 check:"
            tenant_name=self._pnda_env['openstack_parameters']['KEYSTONE_TENANT']
            auth=v2.Password(auth_url=auth_url,username=username, password=password, tenant_name=tenant_name)
        elif int(self._pnda_env['openstack_parameters']['KEYSTONE_AUTH_VERSION']) == 3:
            project_name=self._pnda_env['openstack_parameters']['KEYSTONE_TENANT']
            #user_domain_name=self._pnda_env['openstack_parameters']['KEYSTONE_USER']
            #project_domain_name=self._pnda_env['openstack_parameters']['KEYSTONE_USER']
            auth=v3.Password(auth_url=auth_url,username=username, password=password, project_name=project_name)
        else:
            print "Invalid Auth API version"
            sys.exit(1)
        keystone_session = session.Session(auth=auth)
        kwargs = {
        'auth_url': auth_url,
        'session': keystone_session,
        'auth': auth
        }
        return kwargs
        # heat_session = heat_client.Client(version='1', **kwargs)
        # return heat_session

    def _get_nova_session(self):
        # Keystone authentication
        kwargs = self._get_keystone_session()
        # kwargs = {
        #     'auth_url': auth_url,
        #     'session': keystone_session,
        #     'auth': keystone_auth}
        return nova_client.Client(version='2', **kwargs)

    def _get_heat_session(self):
        kwargs = self._get_keystone_session()
        # kwargs = {
        #     'auth_url': auth_url,
        #     'session': keystone_session,
        #     'auth': keystone_auth}
        return heat_client.Client(version='1', **kwargs)

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
        '''
        Load a node config descriptor from a config.json file in the cloud-formation flavor specific directory
        '''
        node_config_file = file('heat-templates/%s/config.json' % self._flavor)
        config = json.load(node_config_file)
        node_config_file.close()
        return config

    def fill_instance_map(self):
        '''
        Use the NOVA client API to generate a list of the target instances
        '''
        nova_session = self._get_nova_session()
        instances = nova_session.servers.list()
        instance_map = {}
        for instance in instances:

            if not('pnda_cluster' in instance.metadata and instance.metadata[
                'pnda_cluster'] == self._cluster and instance.status == "ACTIVE"):
                continue
        
            private_ip_address = None
            public_ip_address = None
            
            for net, net_details in instance.addresses.items():
                if net != '{}_private_network'.format(self._cluster):
                    continue
                private_ip_address = None
                public_ip_address = None
                for interface in net_details:
                    if interface["OS-EXT-IPS:type"] == "fixed":
                        private_ip_address = interface["addr"]
                    if interface["OS-EXT-IPS:type"] == "floating":
                        public_ip_address = interface["addr"]

            instance_map[instance.metadata['Name']] = {
                "bootstrapped": False,
                "public_ip_address": public_ip_address,
                "ip_address": public_ip_address,
                "private_ip_address": private_ip_address,
                "name": instance.metadata['Name'],
                "node_idx": instance.metadata['node_idx'],
                "node_type": instance.metadata['node_type']
            }
        return instance_map

    def pre_install_pnda(self, node_counts):
        '''
        Use the Openstack heatclient API to launch a stack that PNDA can be installed on
        The stack is defined in template files in the flavor specific heat-template directory
        '''
        #Generate template files
        template_data = self._generate_template_file(
            self._flavor, node_counts['datanodes'], node_counts['opentsdb_nodes'], node_counts['kafka_nodes'], node_counts['zk_nodes'])
        
        stack_name = self._cluster

        heat_session=self._get_heat_session()
        templates_path=os.getcwd() + '/cli/' + '_resources_{}-{}'.format(self._flavor,self._cluster)
        template_file=templates_path + "/pnda.yaml"
        print "Template file is : " + template_file
        env_file=templates_path + "/pnda_env.yaml"
        config, ind, bsi = ruamel.yaml.util.load_yaml_guess_indent(open(env_file))
	parameters = config['parameters']
	parameters['KeyName'] = node_counts['keyname']
        #remove extra parmeters for heat template
        exclude_section = ['INFRASTRUCTURE_TYPE','SSH_KEY','OS_USER','KEYSTONE_AUTH_URL','KEYSTONE_USER','KEYSTONE_PASSWORD','KEYSTONE_TENANT','KEYSTONE_AUTH_URL','KEYSTONE_AUTH_VERSION','KEYSTONE_REGION_NAME']
        for param in exclude_section:
            if param in parameters: del parameters[param]

	ruamel.yaml.round_trip_dump(config, open(env_file, 'w'),
                            indent=ind, block_seq_indent=bsi)
        env_param=[env_file]
        print "Env file is : " + env_file
        tpl_files, tpl_template = template_utils.process_template_path(template_file)
        e_files, e_template = template_utils.process_multiple_environments_and_files(env_paths=env_param)
        files_all=files=dict(list(tpl_files.items()) + list(e_files.items()))
        try:
            status=heat_session.stacks.create(stack_name=stack_name, template=tpl_template ,files=files_all,environment=e_template, timeout_mins=120)
            stack_id=status['stack']['id']
            print status
            print "stack_id : ", stack_id
        except heatclient.exc.HTTPConflict as e:
            error_state = e.error
            print("Stack already exists : " , error_state , stack_name)
            sys.exit(1)

        except heatclient.exc.HTTPBadRequest as e:
            error_state = e.error
            print("Bad request : ", error_state)
            sys.exit(1)

        stack_status = 'CREATING'
        while stack_status not in ['CREATE_COMPLETE']:
            time.sleep(5)
            stack_status_body=heat_session.stacks.get(stack_id)
            stack_status=stack_status_body.stack_status
            CONSOLE.info('Stack status : ' + stack_status)
            if stack_status in ['CREATE_FAILED']:
                sys.exit(1)

        if stack_status != 'CREATE_COMPLETE':
            CONSOLE.error('Stack did not come up, status is: ' + stack_status)
            sys.exit(1)

        self.fill_instance_map()

    def pre_expand_pnda(self, node_counts):
        ''' Use the Openstack heatclient API to expand a stack '''

        #Generate template files
        template_data = self._update_template_file(
            self._flavor, node_counts['datanodes'], node_counts['opentsdb_nodes'], node_counts['kafka_nodes'], node_counts['zk_nodes'])
#           self._es_counts['elk_es_master'], self._es_counts['elk_es_ingest'], self._es_counts['elk_es_data'],
#           self._es_counts['elk_es_coordinator'], self._es_counts['elk_es_multi'], self._es_counts['elk_logstash'])

        stack_name = self._cluster
        heat_session = self._get_heat_session()
        templates_path = os.getcwd() + '/cli/' + '_resources_{}-{}'.format(self._flavor,self._cluster)
        template_file = templates_path + "/pnda.yaml"
        print "Template file is : " + template_file
        env_file = templates_path + "/pnda_env.yaml"
        env_param = [env_file]
        print "Env file is : " + env_file
        tpl_files, tpl_template = template_utils.process_template_path(template_file)
        e_files, e_template = template_utils.process_multiple_environments_and_files(env_paths=env_param)
        files_all = files = dict(list(tpl_files.items()) + list(e_files.items()))

        try:
            heat_session.stacks.update(stack_id=stack_name, template=tpl_template, files=files_all, environment=e_template, timeout_mins=120)
            stack_status_body = heat_session.stacks.get(stack_id = stack_name) 
	    stack_status = stack_status_body.stack_status
            stack_id = stack_status_body.stack_name
            CONSOLE.info('Stack status : ' + stack_status)
            CONSOLE.info('Stack id : ' + stack_id)
        except heatclient.exc.HTTPBadRequest as e:
            error_state = e.error
            print("Bad request : ", error_state)
            sys.exit(1)

        while stack_status not in ['UPDATE_COMPLETE']:
            time.sleep(5)
	    stack_status_body = heat_session.stacks.get(stack_id)
            stack_status = stack_status_body.stack_status
	    CONSOLE.info('Stack status : ' + stack_status)
            if stack_status in ['UPDATE_FAILED']:
	        sys.exit(1)

        if stack_status != 'UPDATE_COMPLETE':
            CONSOLE.error('Stack did not come up, status is: %s', stack_status)
            sys.exit(1)

        self.clear_instance_map_cache()

    def _merge_dicts(self, base, mergein):
        for element in mergein:
            if element not in base or not base[element]:
                base[element] = mergein[element]
            else:
                for child in mergein[element]:
                     # base has priority over mergein, so don't overwrite base elements
                    if child not in base[element]:
                        base[element][child] = mergein[element][child]

    def _update_template_file(self, flavor, datanodes, opentsdbs, kafkas, zookeepers):
        self.datanodes = datanodes
        self.opentsdbs = opentsdbs
        self.kafkas = kafkas
	self.zookeepers = zookeepers
        self.flavor = flavor
        resources_dir = '_resources_{}-{}'.format(self.flavor, self._cluster)
        dest_dir = '{}/{}'.format(os.getcwd()+'/cli', resources_dir)

        if os.path.isdir(dest_dir):
            pnda_env_yaml = dest_dir + "/pnda_env.yaml"
            config, ind, bsi = ruamel.yaml.util.load_yaml_guess_indent(open(pnda_env_yaml))
	    parameters = config['parameters']
            if self.datanodes > parameters['DataNodes']:
	        parameters['DataNodes'] = self.datanodes
	    if self.kafkas > parameters['KafkaNodes']:
                parameters['KafkaNodes'] = self.kafkas
	    ruamel.yaml.round_trip_dump(config, open(pnda_env_yaml, 'w'),
                            indent=ind, block_seq_indent=bsi)
 	else:
	    CONSOLE.error('Stack %s does not exist', self._cluster)
            sys.exit(1)
	     
    def _generate_template_file(self, flavor, datanodes, opentsdbs, kafkas, zookeepers):
        stack_params=[]
        stack_params.append('ZookeeperNodes: {}'.format(zookeepers))
        stack_params.append('KafkaNodes: {}'.format(kafkas))
        stack_params.append('DataNodes: {}'.format(datanodes))
        stack_params.append('OpentsdbNodes: {}'.format(opentsdbs))
        print stack_params

        resources_dir = '_resources_{}-{}'.format(flavor, self._cluster)
        dest_dir = '{}/{}'.format(os.getcwd()+'/cli', resources_dir)
        if os.path.isdir(dest_dir):
            shutil.rmtree(dest_dir)
        os.makedirs(dest_dir)

        include_sections = ['infrastructure', 'openstack_parameters']
        with open( dest_dir + '/pnda_env_openstack.yaml', 'w') as pnda_env_openstack:
            pnda_env_openstack.write('parameters:\n')
            for section in self._pnda_env:
                if section  in include_sections:
                    for setting in self._pnda_env[section]:
                        val = '"%s"' % self._pnda_env[section][setting] if isinstance(self._pnda_env[section][setting], (list, tuple)) else self._pnda_env[section][setting]
                        pnda_env_openstack.write('  %s: %s\n' % (setting, val))
            for instance_node in stack_params:
                print instance_node
                pnda_env_openstack.write('  %s\n' % (instance_node))
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

    def pre_destroy_pnda(self):
        CONSOLE.info('Deleting Openstack stack')
        stack_name = self._cluster
        heat_session=self._get_keystone_session()
        try:
            delete_status=heat_session.stacks.delete(stack_id=stack_name)
            print delete_status
        except heatclient.exc.HTTPConflict as e:
            error_state = e.error
            print("Stack does not exists : " , error_state , stack_name)
            sys.exit(1)

        except heatclient.exc.HTTPBadRequest as e:
            error_state = e.error
            print("Bad request : ", error_state)
            sys.exit(1)
