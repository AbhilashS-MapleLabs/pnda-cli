#!/bin/bash -v

# This script runs on instances with a node_type tag of "tools"
# It sets the roles that determine what software is installed
# on this instance by platform-salt scripts and the minion
# id and hostname

# The pnda_env-<cluster_name>.sh script generated by the CLI should
# be run prior to running this script to define various environment
# variables

set -e

cat >> /etc/salt/grains <<EOF
roles:
  - kafka_manager
  - platform_testing_general
  - elk
EOF

cat >> /etc/salt/minion <<EOF
id: $PNDA_CLUSTER-tools
EOF

echo $PNDA_CLUSTER-tools > /etc/hostname
hostname $PNDA_CLUSTER-tools
