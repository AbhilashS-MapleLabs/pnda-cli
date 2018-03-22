#!/bin/bash -v

# This script runs on instances with a node_type tag of "hadoop-mgr-2"
# It sets the roles that determine what software is installed
# on this instance by platform-salt scripts and the minion
# id and hostname

# The pnda_env-<cluster_name>.sh script generated by the CLI should
# be run prior to running this script to define various environment
# variables

set -e

cat >> /etc/salt/grains <<EOF
hadoop:
  role: MGR02
roles:
  - mysql_connector
EOF

cat >> /etc/salt/minion <<EOF
id: $PNDA_CLUSTER-hadoop-mgr-2
EOF

echo $PNDA_CLUSTER-hadoop-mgr-2 > /etc/hostname
hostname $PNDA_CLUSTER-hadoop-mgr-2
