etcd-aws-cluster
==============

This container serves to assist in the creation of an etcd (2.x) cluster from an AWS auto scaling group. It writes a file to /etc/sysconfig/etcd-peers that contains parameters for etcd:

- ETCD_INITIAL_CLUSTER_STATE
  - either `new` or `existing`   
  - used to specify whether we are creating a new cluster or joining an existing one
- ETCD_NAME
  - the name of the machine joining the etcd cluster
  - this is obtained by getting the instance if from amazon of the host (e.g. i-694fad83)
- ETCD_INITIAL_CLUSTER
  - this is a list of the machines (id and ip) expected to be in the cluster, including the new machine
  - e.g., "i-5fc4c9e1=http://10.0.0.1:2380,i-694fad83=http://10.0.0.2:2380"

This file can then be loaded as an EnvironmentFile in an etcd2 drop-in to properly configure etcd2:

```
[Service]
EnvironmentFile=/etc/sysconfig/etcd-peers
```

Workflow
--------

- get the instance id and ip from amazon
- fetch the autoscaling group this machine belongs to
- obtain the ip of every member of the auto scaling group
- for each member of the autoscaling group detect if they are running etcd and if so who they see as members of the cluster

  if no machines respond OR there are existing peers but my instance id is listed as a member of the cluster  

    - assume that this is a new cluster
    - write a file using the ids/ips of the autoscaling group 
  
  else 

    - assume that we are joining an existing cluster
    - check to see if any machines are listed as being part of the cluster but are not part of the autoscaling group
      -  if so remove it from the etcd cluster  
    - add this machine to the current cluster
    - write a file using the ids/ips obtained from query etcd for members of the cluster


Usage
-----

```docker run -v /etc/sysconfig/:/etc/sysconfig/ MonsantoCo/etcd-aws-cluster```

Demo
----

We have created a CloudFomation script that shows sample usage of this container for creating a simple etcd cluster: https://gist.github.com/tj-corrigan/3baf86051471062b2fb7
