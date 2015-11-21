#! /bin/bash
pkg="etcd-aws-cluster"
version="0.5"
etcd_peers_file_path="/etc/sysconfig/etcd-peers"
region=$(curl -s http://169.254.169.254/latest/dynamic/instance-identity/document | jq --raw-output .region)
if [[ ! $region ]]; then
    echo "$pkg: failed to get region"
    exit 1
fi

# Allow default client/server ports to be changed if necessary
client_port=${ETCD_CLIENT_PORT:-2379}
server_port=${ETCD_SERVER_PORT:-2380}

# ETCD API https://coreos.com/etcd/docs/2.0.11/other_apis.html
add_ok=201
already_added=409
delete_ok=204

#if the script has already run just exit
if [ -f "$etcd_peers_file_path" ]; then
    echo "$pkg: etcd-peers file $etcd_peers_file_path already created, exiting"
    exit 0
fi

ec2_instance_id=$(curl -s http://169.254.169.254/latest/meta-data/instance-id)
if [[ ! $ec2_instance_id ]]; then
    echo "$pkg: failed to get instance id from instance metadata"
    exit 2
fi

ec2_instance_ip=$(curl -s http://169.254.169.254/latest/meta-data/local-ipv4)
if [[ ! $ec2_instance_ip ]]; then
    echo "$pkg: failed to get instance IP address"
    exit 3
fi

# If we're in proxy mode we don't have to look this up and expect an env var
if [[ ! $PROXY_ASG ]]; then
    etcd_proxy=off
    asg_name=$(aws autoscaling describe-auto-scaling-groups --region $region | jq --raw-output ".[] | map(select(.Instances[].InstanceId | contains(\"$ec2_instance_id\"))) | .[].AutoScalingGroupName")
    if [[ ! $asg_name ]]; then
        echo "$pkg: failed to get the auto scaling group name"
        exit 4
    fi
else
    etcd_proxy=on
    asg_name=$PROXY_ASG
fi

etcd_client_scheme=${ETCD_CLIENT_SCHEME:-http}
echo "client_client_scheme=$etcd_client_scheme"

etcd_peer_scheme=${ETCD_PEER_SCHEME:-http}
echo "peer_peer_scheme=$etcd_peer_scheme"

etcd_peer_urls=$(aws ec2 describe-instances --region $region --instance-ids $(aws autoscaling describe-auto-scaling-groups --region $region --auto-scaling-group-name $asg_name | jq '.AutoScalingGroups[0].Instances[] | select(.LifecycleState  == "InService") | .InstanceId' | xargs) | jq -r ".Reservations[].Instances | map(\"$etcd_client_scheme://\" + .NetworkInterfaces[].PrivateIpAddress + \":$client_port\")[]")

if [[ ! $etcd_peer_urls ]]; then
    echo "$pkg: unable to find members of auto scaling group"
    exit 5
fi

echo "etcd_peer_urls=$etcd_peer_urls"

etcd_existing_peer_urls=
etcd_existing_peer_names=
etcd_good_member_url=

for url in $etcd_peer_urls; do
    case "$url" in
        # If we're in proxy mode this is an error, but unlikely to happen?
        *$ec2_instance_ip*) continue;;
    esac

    etcd_members=$(curl $ETCD_CURLOPTS -f -s $url/v2/members)

    if [[ $? == 0 && $etcd_members ]]; then
        etcd_good_member_url="$url"
		echo "etcd_members=$etcd_members"
        etcd_existing_peer_urls=$(echo "$etcd_members" | jq --raw-output .[][].peerURLs[0])
		etcd_existing_peer_names=$(echo "$etcd_members" | jq --raw-output .[][].name)
	break
    fi
done

echo "etcd_good_member_url=$etcd_good_member_url"
echo "etcd_existing_peer_urls=$etcd_existing_peer_urls"
echo "etcd_existing_peer_names=$etcd_existing_peer_names"

# if I am not listed as a member of the cluster assume that this is a existing cluster
# this will also be the case for a proxy situation
if [[ $etcd_existing_peer_urls && $etcd_existing_peer_names != *"$ec2_instance_id"* ]]; then
    echo "joining existing cluster"

    # eject bad members from cluster
    peer_regexp=$(echo "$etcd_peer_urls" | sed 's/^.*https\{0,1\}:\/\/\([0-9.]*\):[0-9]*.*$/contains(\\"\/\/\1:\\")/' | xargs | sed 's/  */ or /g')
    if [[ ! $peer_regexp ]]; then
        echo "$pkg: failed to create peer regular expression"
        exit 6
    fi

    echo "peer_regexp=$peer_regexp"
    bad_peer=$(echo "$etcd_members" | jq --raw-output ".[] | map(select(.peerURLs[] | $peer_regexp | not )) | .[].id")
    echo "bad_peer=$bad_peer"

    if [[ $bad_peer ]]; then
        for bp in $bad_peer; do
            echo "removing bad peer $bp"
            status=$(curl $ETCD_CURLOPTS -f -s -w %{http_code} "$etcd_good_member_url/v2/members/$bp" -XDELETE)
            if [[ $status != $delete_ok ]]; then
                echo "$pkg: ERROR: failed to remove bad peer: $bad_peer, return code $status."
                exit 7
            fi
        done
    fi

    # If we're not a proxy we add ourselves as a member to the cluster
    if [[ ! $PROXY_ASG ]]; then
        etcd_initial_cluster=$(curl $ETCD_CURLOPTS -s -f "$etcd_good_member_url/v2/members" | jq --raw-output '.[] | map(.name + "=" + .peerURLs[0]) | .[]' | xargs | sed 's/  */,/g')$(echo ",$ec2_instance_id=${etcd_peer_scheme}://${ec2_instance_ip}:$server_port")
        echo "etcd_initial_cluster=$etcd_initial_cluster"
        if [[ ! $etcd_initial_cluster ]]; then
            echo "$pkg: docker command to get etcd peers failed"
            exit 8
        fi

        # join an existing cluster
        echo "adding instance ID $ec2_instance_id with IP $ec2_instance_ip"
        status=$(curl $ETCD_CURLOPTS -f -s -w %{http_code} -o /dev/null -XPOST "$etcd_good_member_url/v2/members" -H "Content-Type: application/json" -d "{\"peerURLs\": [\"$etcd_peer_scheme://$ec2_instance_ip:$server_port\"], \"name\": \"$ec2_instance_id\"}")
        if [[ $status != $add_ok && $status != $already_added ]]; then
            echo "$pkg: unable to add $ec2_instance_ip to the cluster: return code $status."
            exit 9
        fi
    # If we are a proxy we just want the list for the actual cluster
    else
        etcd_initial_cluster=$(curl $ETCD_CURLOPTS -s -f "$etcd_good_member_url/v2/members" | jq --raw-output '.[] | map(.name + "=" + .peerURLs[0]) | .[]' | xargs | sed 's/  */,/g')
        echo "etcd_initial_cluster=$etcd_initial_cluster"
        if [[ ! $etcd_initial_cluster ]]; then
            echo "$pkg: docker command to get etcd peers failed"
            exit 8
        fi
    fi

    cat > "$etcd_peers_file_path" <<EOF
ETCD_INITIAL_CLUSTER_STATE=existing
ETCD_NAME=$ec2_instance_id
ETCD_INITIAL_CLUSTER="$etcd_initial_cluster"
ETCD_PROXY=$etcd_proxy
EOF

# otherwise I was already listed as a member so assume that this is a new cluster
else
    # create a new cluster
    echo "creating new cluster"

    etcd_initial_cluster=$(aws ec2 describe-instances --region $region --instance-ids $(aws autoscaling describe-auto-scaling-groups --region $region --auto-scaling-group-name $asg_name | jq .AutoScalingGroups[0].Instances[].InstanceId | xargs) | jq -r ".Reservations[].Instances | map(.InstanceId + \"=$etcd_peer_scheme://\" + .NetworkInterfaces[].PrivateIpAddress + \":$server_port\")[]" | xargs | sed 's/  */,/g')
    echo "etcd_initial_cluster=$etcd_initial_cluster"
    if [[ ! $etcd_initial_cluster ]]; then
        echo "$pkg: unable to get peers from auto scaling group"
        exit 10
    fi

    cat > "$etcd_peers_file_path" <<EOF
ETCD_INITIAL_CLUSTER_STATE=new
ETCD_NAME=$ec2_instance_id
ETCD_INITIAL_CLUSTER="$etcd_initial_cluster"
EOF
fi

exit 0
