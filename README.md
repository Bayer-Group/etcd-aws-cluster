etcd-aws-cluster
==============

This container serves to assist in the creation of an etcd (2.x) cluster from an AWS auto scaling group. Using this, your Etcd cluster will
update its membership whenever an instance is added to the autoscale group. It writes a file to /etc/sysconfig/etcd-peers that contains
parameters for etcd:

- `ETCD_INITIAL_CLUSTER_STATE`
  - either `new` or `existing`
  - used to specify whether we are creating a new cluster or joining an existing one
- `ETCD_NAME`
  - the name of the machine joining the etcd cluster
  - this is obtained by getting the instance if from amazon of the host (e.g. i-694fad83)
- `ETCD_INITIAL_CLUSTER`
  - this is a list of the machines (id and ip) expected to be in the cluster, including the new machine
  - e.g., `"i-5fc4c9e1=http://10.0.0.1:2380,i-694fad83=http://10.0.0.2:2380"`

This file can then be loaded as an EnvironmentFile in an etcd2 drop-in to properly configure etcd2:

```
[Service]
EnvironmentFile=/etc/sysconfig/etcd-peers
```

Workflow
--------

- Get the instance id and ip from Amazon
- Fetch the autoscaling group this machine belongs to
- Obtain the ip of every member of the autoscaling group
- For each member of the autoscaling group detect if they are running etcd and if so who they see as members of the cluster

  If no machines respond **or** there are existing peers but my instance id is listed as a member of the cluster

    - Assume that this is a new cluster
    - Write a file using the ids/ips of the autoscaling group
  
  else 

    - Assume that we are joining an existing cluster
    - Check to see if any machines are listed as being part of the cluster but are not part of the autoscaling group
      -  If so remove it from the etcd cluster
    - Add this machine to the current cluster
    - Write a file using the ids/ips obtained from query etcd for members of the cluster

- If DNS is configured, service records are created/updated so that proxies will be able to locate the cluster on their own

Usage
-----

```docker run -v /etc/sysconfig/:/etc/sysconfig/ waisbrot/etcd-aws-cluster```

Environment Variables

| Name             | Default                     | Description                                                        |
| ---              | ---                         | ---                                                                |
| `PEERS_FILE`     | `/etc/sysconfig/etcd-peers` | The file to write out peer-config info                             |
| `CLIENT_SCHEME`  | `http`                      | Either `http` or `https`                                           |
| `PEER_SCHEME`    | `http`                      | Either `http` or `https`                                           |
| `CLIENT_PORT`    | 2379                        | Where Etcd listens for clients                                     |
| `PEER_PORT`      | 2380                        | Where Etcd listens for peers                                       |
| `MAX_RETRIES`    | 10                          | Number of times to retry joining a cluster or ejecting a bad node  |
| `HOSTED_ZONE_ID` | None                        | If present, Etcd will be configured for DNS discovery in this zone |
| `DOMAIN_NAME`    | None                        | If present, Etcd will be configured for DNS discovery with this name |


None of these variables need to be set (the defaults will work), but you do need to set the last two for proxies to be able to discover the
cluster.
