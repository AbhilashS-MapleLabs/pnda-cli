#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#   Copyright (c) 2016 Cisco and/or its affiliates.
#   This software is licensed to you under the terms of the Apache License, Version 2.0
#   (the "License").
#   You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
#   The code, technical concepts, and all information contained herein, are the property of
#   Cisco Technology, Inc.and/or its affiliated entities, under various laws including copyright,
#   international treaties, patent, and/or contract.
#   Any use of the material herein must be in accordance with the terms of the License.
#   All rights not expressly granted by the License are reserved.
#   Unless required by applicable law or agreed to separately in writing, software distributed
#   under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF
#   ANY KIND, either express or implied.
#
#   Purpose: Script to create PNDA

import sys
import os
import os.path
import atexit
import datetime
import pnda_cli_utils as utils
from pnda_cli_utils import PNDAConfigException

import yaml

from validation import UserInputValidator
from backend_cloud_formation import CloudFormationBackend
from backend_existing_machines import ExistingMachinesBackend

utils.init_logging()
CONSOLE = utils.CONSOLE_LOGGER
LOG = utils.FILE_LOGGER
LOG_FILE_NAME = utils.LOG_FILE_NAME

PNDA_ENV = None
START = datetime.datetime.now()

def banner():
    print r"    ____  _   ______  ___ "
    print r"   / __ \/ | / / __ \/   |"
    print r"  / /_/ /  |/ / / / / /| |"
    print r" / ____/ /|  / /_/ / ___ |"
    print r"/_/   /_/ |_/_____/_/  |_|"
    print r""

@atexit.register
def display_elasped():
    blue = '\033[94m'
    reset = '\033[0m'
    elapsed = datetime.datetime.now() - START
    CONSOLE.info("%sTotal execution time: %s%s", blue, str(elapsed), reset)

def get_requested_node_counts(deployment_target):
    # This function counts the number of machines that exist for each node type
    return get_node_counts(deployment_target, False)

def get_live_node_counts(deployment_target):
    # This function counts the number of machines that have been bootstrapped into a salt cluster for each node type
    return get_node_counts(deployment_target, True)

def get_node_counts(deployment_target, live_only):
    # This function counts the number of machines for each node type, optionally limiting to live (bootstrapped) nodes only
    CONSOLE.debug('Counting %s instances', 'live' if live_only else 'all')

    node_counts = {'zk':0, 'kafka':0, 'hadoop-dn':0, 'opentsdb':0}
    for _, instance in deployment_target.get_instance_map(live_only).iteritems():
        if len(instance['node_type']) > 0:
            if instance['node_type'] in node_counts:
                current_count = node_counts[instance['node_type']]
            else:
                current_count = 0
            if not live_only or instance['bootstrapped']:
                node_counts[instance['node_type']] = current_count + 1
    return node_counts

def check_config_file():
    if not os.path.exists('pnda_env.yaml'):
        CONSOLE.error('Missing required pnda_env.yaml config file, make a copy of pnda_env_example.yaml named pnda_env.yaml, fill it out and try again.')
        if not os.path.exists('pnda_cloud_infra.yaml'):
            CONSOLE.error('Missing required pnda_cloud_infra.yaml config file, make a copy of pnda_cloud_infra_example.yaml named pnda_cloud_infra.yaml, fill it out and try again.')
        sys.exit(1)

def write_pnda_env_sh(cluster):
    client_only = ['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'PLATFORM_GIT_BRANCH']
    with open('cli/pnda_env_%s.sh' % cluster, 'w') as pnda_env_sh_file:
        for section in PNDA_ENV:
            for setting in PNDA_ENV[section]:
                if setting not in client_only:
                    val = '"%s"' % PNDA_ENV[section][setting] if isinstance(PNDA_ENV[section][setting], (list, tuple)) else PNDA_ENV[section][setting]
                    pnda_env_sh_file.write('export %s=%s\n' % (setting, val))

def valid_flavors():
    cfn_dirs = [dir_name for dir_name in os.listdir('../cloud-formation') if  os.path.isdir(os.path.join('../cloud-formation', dir_name))]
    bootstap_dirs = [dir_name for dir_name in os.listdir('../bootstrap-scripts') if  os.path.isdir(os.path.join('../bootstrap-scripts', dir_name))]

    return list(set(cfn_dirs + bootstap_dirs))

def select_deployment_target_impl(fields):
    #pylint: disable=redefined-variable-type
    if fields['x_machines_definition'] is not None:
        CONSOLE.info('Installing to existing infra, defined in %s', fields['x_machines_definition'])
        deployment_target = ExistingMachinesBackend(
            PNDA_ENV, fields['pnda_cluster'], fields["no_config_check"], fields['flavor'], fields['keyname'], fields['branch'], fields['x_machines_definition'])
        node_counts = get_requested_node_counts(deployment_target)
        fields['datanodes'] = node_counts['hadoop-dn']
        fields['opentsdb_nodes'] = node_counts['opentsdb']
        fields['kafka_nodes'] = node_counts['kafka']
        fields['zk_nodes'] = node_counts['zk']
    else:
        os.environ['AWS_ACCESS_KEY_ID'] = PNDA_ENV['aws_parameters']['AWS_ACCESS_KEY_ID']
        os.environ['AWS_SECRET_ACCESS_KEY'] = PNDA_ENV['aws_parameters']['AWS_SECRET_ACCESS_KEY']
        print 'Using ec2 credentials:'
        print '  AWS_REGION = %s' % PNDA_ENV['aws_parameters']['AWS_REGION']
        print '  AWS_ACCESS_KEY_ID = %s' % PNDA_ENV['aws_parameters']['AWS_ACCESS_KEY_ID']
        print '  AWS_SECRET_ACCESS_KEY = %s' % PNDA_ENV['aws_parameters']['AWS_SECRET_ACCESS_KEY']
        if not os.path.isfile('git.pem'):
            with open('git.pem', 'w') as git_key_file:
                git_key_file.write('If authenticated access to the platform-salt git repository is required then' +
                                   ' replace this file with a key that grants access to the git server.\n\n' +
                                   'Set PLATFORM_GIT_REPO_HOST and PLATFORM_GIT_REPO_URI in pnda_env.yaml, for example:\n' +
                                   'PLATFORM_GIT_REPO_HOST: github.com\n' +
                                   'PLATFORM_GIT_REPO_URI: git@github.com:pndaproject/platform-salt.git\n')
        deployment_target = CloudFormationBackend(
            PNDA_ENV, fields['pnda_cluster'], fields["no_config_check"], fields['flavor'], fields['keyname'], fields['branch'], fields['dry_run'])
    return deployment_target

def merge_dicts(pnda_env,target_env):
    target_platform=target_env['cloud_infrastructure']['CLOUD_INFRASTRUCTURE_TYPE']
    
    section_list=['cloud_infrastructure']
    
    if target_platform == 'aws':
	section_list.append('aws_parameters')
    elif target_platform == 'openstack':
        section_list.append('openstack_parameters')
    elif target_platform == 'existing-machines':
        section_list.append('existing_machines_parameters')
    for section in section_list:
        if section in target_env:
            pnda_env[section]=target_env[section]
    
def main():
    print 'Saving debug log to %s' % LOG_FILE_NAME

    if not os.path.basename(os.getcwd()) == "cli":
        print 'Please run from inside the /cli directory'
        sys.exit(1)

    ###
    # Process user input
    ###
    input_validator = UserInputValidator(valid_flavors())
    fields = input_validator.parse_user_input()
    utils.init_runfile(fields['pnda_cluster'])

    os.chdir('../')

    ###
    # Process & validate YAML configuration
    # TODO: refactor out in a similar way to user input validation and share common code
    ###

    global PNDA_ENV
    global TARGET_ENV
    check_config_file()
    with open('pnda_env.yaml', 'r') as infile:
        PNDA_ENV = yaml.load(infile)
    with open('pnda_cloud_infra.yaml', 'r') as infile_target:
        TARGET_ENV = yaml.load(infile_target)

    target_platform=TARGET_ENV['cloud_infrastructure']['CLOUD_INFRASTRUCTURE_TYPE']
    #TODO: validate target_platform options

    merge_dicts(PNDA_ENV,TARGET_ENV)

    es_fields = {
        "elk_es_master":PNDA_ENV['elk-cluster']['MASTER_NODES'],
        "elk_es_data":PNDA_ENV['elk-cluster']['DATA_NODES'],
        "elk_es_ingest":PNDA_ENV['elk-cluster']['INGEST_NODES'],
        "elk_es_coordinator":PNDA_ENV['elk-cluster']['COORDINATING_NODES'],
        "elk_es_multi":PNDA_ENV['elk-cluster']['MULTI_ROLE_NODES'],
        "elk_logstash":PNDA_ENV['elk-cluster']['LOGSTASH_NODES']
    }

    # TODO parsing and validation of YAML needs to be factored out
    range_validator = input_validator.get_range_validator()
    try:
        for field, val in es_fields.items():
            numeric_val = int(val) if val is not None else 0
            if range_validator is not None and not range_validator.validate_field(field, numeric_val):
                raise PNDAConfigException("Error in pnda_env.yaml: %s must be in range (%s)" % (field, range_validator.get_validation_rule(field)))
            es_fields[field] = numeric_val
    except ValueError:
        raise PNDAConfigException("Error in pnda_env.yaml: %s must be a number" % field)

    # Branch defaults to master
    # but may be overridden by pnda_env.yaml
    # and both of those are overridden by --branch
    branch = 'master'
    if 'PLATFORM_GIT_BRANCH' in PNDA_ENV['platform_salt']:
        branch = PNDA_ENV['platform_salt']['PLATFORM_GIT_BRANCH']
    if fields['branch'] is not None:
        branch = fields['branch']
    fields['branch'] = branch

    deployment_target = select_deployment_target_impl(fields)

    write_pnda_env_sh(fields['pnda_cluster'])

    ###
    # Destroy command
    ###
    if fields['command'] == 'destroy':
        deployment_target.destroy()
        sys.exit(0)

    ###
    # Expand command
    ###
    if fields['command'] == 'expand':
        do_orchestrate = False
        deployment_target.clear_instance_map_cache()
        node_counts = get_live_node_counts(deployment_target)

        # if these fields not supplied, default to previous values
        if fields['datanodes'] is None:
            fields['datanodes'] = node_counts['hadoop-dn']
        if fields['kafka_nodes'] is None:
            fields['kafka_nodes'] = node_counts['kafka']

        if fields['datanodes'] < node_counts['hadoop-dn']:
            print "You cannot shrink the cluster using this CLI, existing number of datanodes is: %s" % node_counts['hadoop-dn']
            sys.exit(1)
        elif fields['datanodes'] > node_counts['hadoop-dn']:
            print "Increasing the number of datanodes from %s to %s" % (node_counts['hadoop-dn'], fields['datanodes'])
            do_orchestrate = True
        if fields['kafka_nodes'] < node_counts['kafka']:
            print "You cannot shrink the cluster using this CLI, existing number of kafkanodes is: %s" % node_counts['kafka']
            sys.exit(1)
        elif fields['kafka_nodes'] > node_counts['kafka']:
            print "Increasing the number of kafkanodes from %s to %s" % (node_counts['kafka'], fields['kafka_nodes'])

        # Does not support changing the following during an expand
        fields['opentsdb_nodes'] = node_counts['opentsdb']
        fields['zk_nodes'] = node_counts['zk']

        deployment_target.expand(fields, do_orchestrate)
        sys.exit(0)

    ###
    # Create command
    ###
    if fields['command'] == 'create':
        console_dns = deployment_target.create(fields)
        CONSOLE.info('Use the PNDA console to get started: http://%s', console_dns)
        CONSOLE.info(' Access hints:')
        CONSOLE.info('  - The script ./socks_proxy-%s sets up port forwarding to the PNDA cluster with SSH acting as a SOCKS server on localhost:9999',
                     fields['pnda_cluster'])
        CONSOLE.info('  - Please review ./socks_proxy-%s and ensure it complies with your local security policies before use', fields['pnda_cluster'])
        CONSOLE.info('  - Set up a socks proxy with: chmod +x socks_proxy-%s; ./socks_proxy-%s', fields['pnda_cluster'], fields['pnda_cluster'])
        CONSOLE.info('  - SSH to a node with: ssh -F ssh_config-%s <private_ip>', fields['pnda_cluster'])
        sys.exit(0)

if __name__ == "__main__":
    try:
        main()
    except Exception as exception:
        CONSOLE.error(exception)
        raise
