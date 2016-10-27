import boto3.session
import requests
import os.path as path
import logging
import json
import collections
import re
import textwrap
import time
import os

LOG = logging.getLogger(__name__)

PEERS_FILE = os.getenv('PEERS_FILE', '/etc/sysconfig/etcd-peers')

CLIENT_SCHEME = os.getenv('CLIENT_SCHEME', 'http')
PEER_SCHEME = os.getenv('PEER_SCHEME', 'http')

CLIENT_PORT = int(os.getenv('CLIENT_PORT', 2379))
PEER_PORT = int(os.getenv('PEER_PORT', 2380))

MAX_RETRIES = int(os.getenv('MAX_RETRIES', 10))

HOSTED_ZONE_ID = os.getenv('HOSTED_ZONE_ID', None)
DOMAIN_NAME = os.getenv('DOMAIN_NAME', None)

boto = None

class InstanceMetadata:
    def __init__(self):
        def get_metadata(path):
            response = requests.get('http://169.254.169.254{}'.format(path)).text
            response.strip()
            return response

        def get_metadata_dict(path):
            return json.loads(get_metadata(path))

        LOG.debug("Fetching instance metadata")
        doc = get_metadata_dict('/latest/dynamic/instance-identity/document')
        self.region = doc['region']
        self.instance_id = get_metadata('/latest/meta-data/instance-id')
        self.instance_ip = get_metadata('/latest/meta-data/local-ipv4')
        global boto
        boto = boto3.session.Session(region_name=self.region)
        LOG.info('This instance ID = %s, IP = %s', self.instance_id, self.instance_ip)
        LOG.debug("Done fetching instance metadata")

class LocalGroup:
    def __init__(self, instance_id):
        asg = boto.client('autoscaling')
        ec2 = boto.resource('ec2')

        # find our group's name
        response = asg.describe_auto_scaling_instances(InstanceIds=[instance_id])
        self.name = response['AutoScalingInstances'][0]['AutoScalingGroupName']
        LOG.debug('ASG name: %s', self.name)

        # find our peer group
        response = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[self.name])
        self.asg_peers = []
        for inst in response['AutoScalingGroups'][0]['Instances']:
            peer_id = inst['InstanceId']
            LOG.debug('ASG peer %s has state %s', peer_id, inst['LifecycleState'])
            if inst['LifecycleState'] == 'InService' and peer_id != instance_id:
                self.asg_peers.append(ec2.Instance(peer_id))
        LOG.info('Found %s ASG peers', len(self.asg_peers))

    def peer_ips(self):
        for peer in self.asg_peers:
            yield peer.private_ip_address

    def peer_nodes(self):
        LOG.debug('transform %s peers into Etcd nodes', len(self.asg_peers))
        return [ EtcdNode(id=None, name=peer.instance_id, peerURLs=[EtcdNode.peer_url_from_ip(peer.private_ip_address)], clientURLs=[EtcdNode.client_url_from_ip(peer.private_ip_address)]) for peer in self.asg_peers ]

class EtcdNode(collections.namedtuple('EtcdNode', 'id, name, peerURLs, clientURLs')):
    ejected = False
    def peer_url_from_ip(ip):
        return '{}://{}:{}'.format(PEER_SCHEME, ip, PEER_PORT)
    def client_url_from_ip(ip):
        return '{}://{}:{}'.format(CLIENT_SCHEME, ip, CLIENT_PORT)
    def peer_ip(self):
        re.search('://(.*):', peer_url()).group(1)
    def peer_url(self):
        return self.peerURLs[0]
    def client_ip(self):
        re.search('://(.*):', client_url()).group(1)
    def client_url(self):
        return self.clientURLs[0]
    def __repr__(self):
        return 'EtcdNode(id={}, name={}, peerURLs={}, clientURLs={})'.format(repr(self.id), repr(self.name), self.peerURLs, self.clientURLs)

class EtcdCluster:
    def __init__(self, peer_candidates, my_id, my_ip):
        # try each address and see if it can tell us about an existing cluster
        self.existing_cluster = False
        self.peers = []
        self.candidates = peer_candidates

        my_peer_url = '{}://{}:{}'.format(PEER_SCHEME, my_ip, PEER_PORT)
        my_client_url = '{}://{}:{}'.format(CLIENT_SCHEME, my_ip, CLIENT_PORT)
        self.me = EtcdNode(id=None, name=my_id, peerURLs=[my_peer_url], clientURLs=[my_client_url])
        LOG.debug('This node = %s, candidates = %s', self.me, self.candidates)

        self._hunt_for_cluster()

    def _hunt_for_cluster(self):
        """
        Given a list of IP addresses, see if any of them will tell us about an existing
        cluster. Update the object with the results of the hunt.
        """
        for peer in self.candidates:
            client_url = peer.client_url()
            LOG.debug('hunting for a Etcd cluster: %s', client_url)
            try:
                response = requests.get('{}/v2/members'.format(client_url), timeout=3).json()
                found_members = response['members']
                self.existing_cluster = True
                LOG.debug('found a cluster of %s at %s: %s', len(found_members), client_url, found_members)
                for member in found_members:
                    node = EtcdNode(**member)
                    self.peers.append(node)
                    if client_url in node.clientURLs:
                        LOG.debug('node %s matches %s', node, client_url)
                        self.live_node = node  # the one that answered us
                    else:
                        LOG.debug('node %s does not match %s', node, client_url)
                return
            except Exception as err:
                LOG.debug('No Etcd found on %s (%s)', client_url, err)
                continue # it's OK if some (or all!) of the machines do not answer us

    def eject_orphans(self):
        """
        Given a list of IPs, eject any etcd node that's not in the list.
        """
        if not self.existing_cluster:
            return

        def peer_url_to_ip(url):
            match = re.search('https?://([0-9.]+):', url)
            return match.group(1)

        def eject_orphan(node_id, tries=0):
            """
            Attempt to delete the given node from the cluster
            """
            if tries > MAX_RETRIES:
                raise Exception('Too many retries trying to delete node {} from the cluster'.format(node_id))
            delete_url = '{}/v2/members/{}'.format(self.live_node.client_url(), node_id)
            LOG.debug('ejecting %s from the Etcd cluster: %s', node_id, delete_url)
            response = requests.delete(delete_url)
            if response.status_code == 204 or response.status_code == 410:
                return
            else:
                LOG.debug('status_code = %s, retry', response.status_code)
                time.sleep(1)
                eject_orphan(node_id, tries=tries+1)

        LOG.debug('looking for Etcd members that are not part of the ASG')
        for node in self.peers:
            should_eject = True
            for url in node.peerURLs:
                # if the node listens on several interfaces, we might only have one appear in the ASG
                for candidate in self.candidates:
                    if url in candidate.peerURLs:
                        should_eject = False
            if should_eject:
                eject_orphan(node.id)
                node.ejected = True

        self.peers = [peer for peer in self.peers if not peer.ejected]

    def _build_initial_cluster_string(self, nodes):
        """
        Build an `initial_cluster` string based on an existing cluster
        """
        LOG.debug('building a cluster string of %s nodes', len(nodes))
        return ','.join([ '{}={}'.format(node.name, node.peer_url()) for node in nodes ])

    def join(self):
        """
        Join an existing cluster
        """
        def join_node(tries=0):
            if tries > MAX_RETRIES:
                raise Exception('Too many retries trying to join the cluster at %s', self.live_node)
            members_url = '{}/v2/members'.format(self.live_node.client_url())
            new_member = {  # only these pieces are required to bootstrap
                'name': self.me.name,
                'peerURLs': self.me.peerURLs,
                'clientURLs': self.me.clientURLs,
            }
            result = requests.post(members_url, json=new_member)
            if result.status_code == 201 or result.status_code == 409:
                LOG.info("Joined cluster")
            else:
                time.sleep(1)
                join_node(tries=tries+1)

        join_node()

        self.cluster_members = self.peers
        self.cluster_state = 'existing'

    def create(self):
        """
        Create a new cluster, based on an autoscale group
        """
        LOG.debug('create a new cluster from %s', self.candidates)
        self.cluster_members = self.candidates
        self.cluster_state = 'new'

    def write_cluster_variables(self, fileobject):
        self.initial_cluster_string = self._build_initial_cluster_string(self.cluster_members)
        fileobject.write(textwrap.dedent('''
        ETCD_INITIAL_CLUSTER_STATE={}
        ETCD_NAME={}
        ETCD_INITIAL_CLUSTER={}
        ETCD_PROXY=off
        '''.format(self.cluster_state, self.me.name, self.initial_cluster_string)))

    def update_dns(self):
        if not HOSTED_ZONE_ID:
            LOG.warning('No HOSTED_ZONE_ID set so no DNS records will be written')
            return
        if not DOMAIN_NAME:
            LOG.warning('No DOMAIN_NAME set so no DNS records will be written')
            return

        route53 = boto.client('route53')
        request = {
            'HostedZoneId': HOSTED_ZONE_ID,
            'ChangeBatch': {
                'Comment': 'Used by the Etcd cluster to advertise to proxies',
                'Changes': [
                    {
                        'Action': 'UPSERT',
                        'ResourceRecordSet': {
                            'Name': '_etcd-server._tcp.{}.'.format(DOMAIN_NAME),
                            'Type': 'SRV',
                            'TTL': 30,
                            'ResourceRecords': [ {'Value': '0 0 {} {}'.format(PEER_PORT, node.peer_ip())} for node in self.cluster_members ]
                        }
                    },
                    {
                        'Action': 'UPSERT',
                        'ResourceRecordSet': {
                            'Name': '_etcd-client._tcp.{}.'.format(DOMAIN_NAME),
                            'Type': 'SRV',
                            'TTL': 30,
                            'ResourceRecords': [ {'Value': '0 0 {} {}'.format(CLIENT_PORT, node.client_ip())} for node in self.cluster_members ]
                        }
                    }
                ]
            }
        }
        route53.change_resource_record_sets(**request)

def main():
    logging.basicConfig()
    logging.getLogger().setLevel(logging.DEBUG)
    for name in ['boto3', 'botocore', 'requests']:
        logging.getLogger(name).setLevel(logging.WARNING)
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(func):%(lineno) - %(message)s')
    ch.setFormatter(formatter)
    LOG.addHandler(ch)


    LOG.info('starting etcd cluster search')

    if path.isfile(PEERS_FILE):
        LOG.info('Peers file %s already exists', PEERS_FILE)
        return

    metadata = InstanceMetadata()
    group = LocalGroup(metadata.instance_id)
    cluster = EtcdCluster(group.peer_nodes(), metadata.instance_id, metadata.instance_ip)
    if cluster.existing_cluster:
        cluster.eject_orphans()
        cluster.join()
    else:
        cluster.create()

    with open(PEERS_FILE, 'w') as out:
        cluster.write_cluster_variables(out)

    cluster.update_dns()

    LOG.info('Done')

main()
