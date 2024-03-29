# Automated Floating/Virtual IP MultiAZ solution on EKS

This post explains the patterns to deploy a Multus based workload using floating/virtual IPs. These floating IPs can failover across AZs as we define more specific routing in the VPC.

Please refer to [blog](https://aws.amazon.com/blogs/industries/automated-application-failover-across-availability-zones-with-floating-virtual-ip-on-amazon-eks/) for additional details.

## Infra Setup

### Pre-requisite
EKS cluster
Bastion node with docker and git

## EKS Node group setup

Clone this repo and you can use the sample CloudFormation template eks-multiaz-nodegroup-multus.yaml to create the nodegroup in 2 AZs.
In this setup worker node groups are deployed in two AZs (Availability zones). Each Worker node group is behind an autoscaling group, per availability zone, providing resiliency for node failures in that availability zone. 
Note: Worker nodegroups use same node Selector labels, allowing application to be scheduled on the workers, irrespective of their availability zones. 

```
git clone https://github.com/aws-samples/eks-automated-multiaz-floating-ip.git
```

Create the EKS Multus nodegroup creation in 2 AZs using cloud formation template in the git repo. Forr parameter inputs, you can refer to help text or https://github.com/aws-samples/eks-install-guide-for-multus/blob/main/cfn/templates/nodegroup/.

***Note:*** Please ensure that security group used by the primary interfaces across Nodegroups are either same or allow the inbound traffic from the VPC CNI subnets used in the nodegroups. If inbound rules in the security groups are not allowed, you would face intercommunication issues between pods. 

## Multi AZ Floating IP Solution on EKS

This solution works as an addon initContainer or sidecar container (vipmanager) with your application, so your application doesn't have to change. Same image can be used for both cases.
You can enable the floating IP across AZ with two patterns. One is using non-VPC IP addresses (floating IP is not part of VPC), second option is using the VPC IP addresses, where we would have to override the subnet routings in VPC. 

### Container Image preparation

You can build the container image with below steps. please change the account number and region 

```
 docker build -t xxxxxxxxxxxx.dkr.ecr.us-east-2.amazonaws.com/vipmanager:0.1 .
```

Create the ECR repo and push the image to ECR

```
aws ecr get-login-password --region us-east-2 | docker login --username AWS --password-stdin xxxxxxxxxxxx.dkr.ecr.us-east-2.amazonaws.com
aws ecr create-repository --repository-name vipmanager --region us-east-2
docker push xxxxxxxxxxxx.dkr.ecr.us-east-2.amazonaws.com/vipmanager:0.1
```
### Container Configuration using Config map 

This container reads configurations from a configmap (vipmanager-config), below are the description and creation command

EX:
```
  RunAsSidecar: "False"
  UseSBR: "True"
  SubnetBasedLoopbacks: "False"
  Intf1Peers: "11.0.0.0/8"
  Intf2Peers: "20.0.0.0/8"
  VPCRTTag: "ALL"
```
RunAsSidecar --> This config indicates whether this container has to run as initContainer (False) or as a sidecar (True).

UseSBR --> This config indicates whether you want to define source based routing for your multus interfaces (net1,net2). if you enable it (True), then you dont need to add static routes for your peers (Intf1Peers, Intf2Peers) as it adds default route for each interface to reply from that interface. if you disable it (False) then your multus interfaces would need the static routes to respond to incoming traffic from peers(Intf1Peers, Intf2Peers)

SubnetBasedLoopbacks --> This config is to indicate, if pattern 1 (non-vpc IP addresses are used for multus) or pattern 2 (VPC IP addresses used for multus). For pattern 1 set as False, and for pattern 2 set True.

Intf1Peers --> This represents the peer network/hosts communicating with the 1st interface(net1/eth1) of the Multus pod 

Intf2Peers --> This represents the peer network/hosts communicating with the 2nd interface(net2/eth2) of the Multus pod. 

VPCRTTag --> This config represents the TAGs on the vpc routing table, to select and update the routes. If absent or "ALL" value is provided then all the routing tables in that VPC are updated with the routes.


```
kubectl apply -f vipmanager-cm.yaml
```

### Solution 1: Using non-VPC Floating IP Addresses

In this solution, a sample pod is using 2 non-vpc IP address(es) as the floating IPs, and assigned to a pod on the secondary interfaces as shown below with IP 192.168.0.2 and 192.168.1.2 on eth1 and eth2 via Multus and ipvlan. 
For simplicity in this example, we are using a single Pod, which runs on a worker in az1. You can also see, that VPC routing table gets updated with the ENI id of the eth1 and eth2 interface of the worker node for the destination floating IPs (192.168.0.2 and 192.168.1.2). 

The vipmanager container does the route addition in this case, based on the Peers provided in the above configmap. In this case, we will be setting the VPC subnet default gateway as the route gateway, which is outside the pod multus network.

#### Multus Network Attachment - non-VPC Floating IP Addresses

In this case, we create multus network-interface-attachment definition using ipvlan and host-local (you can use other ipam as well). In the network attachment, for simplicity, we are just creating a range of 1 IP address. 

Note: In the multus net-attach-def, you dont need to define a route & gw. Actual routes are added by the vipmanager container, based on the peer configuration in vipmanager-config configmap(UseSBR or via Intf1Peers,Intf2Peers ). 

```
kubectl apply -f nonvpc/nad-1.yaml
kubectl apply -f nonvpc/nad-2.yaml
```

#### Floating IP (vipmanager) container deployment 
#### vipmanager as initContainer 


If you are deploying the vimpmanager container as initContainer, ensure that your configmap (vipmanager-config) has below settings

```
  RunAsSidecar: "False"
  SubnetBasedLoopbacks: "False"
```

Add the vip manager container as initContainer (example below), in your deploymentset/statefulset. 

```
      initContainers:
      - name: vipmanager
        image: xxxxxxxxxxxx.dkr.ecr.us-east-2.amazonaws.com/vipmanager:0.1
        imagePullPolicy: IfNotPresent
        securityContext:
          capabilities:
            add: ["NET_ADMIN"]
        args: [/bin/sh, -c, '/app/script.sh']
        envFrom:
              - configMapRef:
                  name: vipmanager-config
```
you can also refer to sample deployment-initContainer.yaml in this git.

```
kubectl apply -f deployment-initContainer.yaml
```
#### vipmanager as sidecar container

If you are deploying the vimpmanager container as sidecar container, ensure that your configmap (vipmanager-config) has below settings

```
  RunAsSidecar: "True"
  SubnetBasedLoopbacks: "False"
```
 
Add the vip manager container as an another container (example below), in your deploymentset/statefulset. 
```
      containers:
      - name: vipmanager
        image: xxxxxxxxxxxx.dkr.ecr.us-east-2.amazonaws.com/vipmanager:0.1
        imagePullPolicy: IfNotPresent
        securityContext:
          capabilities:
            add: ["NET_ADMIN"]
        args: [/bin/sh, -c, '/app/script.sh']
        envFrom:
              - configMapRef:
                  name: vipmanager-config
```
you can also refer to sample deployment-sidecar.yaml in this git.

```
kubectl apply -f deployment-sidecar.yaml
```

Once the containers are deployed you can test the ping or any other traffic (based on the secgroup rules) from one of the above configured peers. you can also check the routes in the vpc route tables, for the ENI route.
You can also validate the failover of the pod on the worker node on az2, by either restarting the pod or cordoning the node.

### Solution 2: Using VPC Floating IP Addresses 

The non-vpc, floating IP solution is usually preferred as it doesn’t mix with VPC Ip addresses & routing, furthermore it provides a clear separation between VPC and non-VPC IP addresses.  In some cases, you might not want to manage, configure and automate such non-vpc IP address space separately. In such cases, if you prefer to use the VPC IP addresses for the floating IP address across availability zones, then with some additional steps, you can achieve the same routing results.

Below are the steps:
1.	Create dummy Floating IP subnets in any availability zone, Ex: 10.10.254.0/28 and 10.10.254.16/28 (/28 is the smallest subnet size)
2.	you can pick any IP (other than network & broadcast IP address), from this subnet as your floating ip (Ex: 10.10.254.2 and 10.10.254.18) 
3.	Do not use these dummy subnets for any instance/ENI creation, as we will override the routing of these subnets in the vpc. To avoid accidental DHCP assignment, you can also use subnet cidr reservation to reserve the subnet cidr.

In this sample, a sample pod is using 2 vpc IP addresses from above subnets as the floating IPs, and assigned to a pod on the secondary interfaces with IP 10.10.254.2 and 10.10.254.18 on eth1 and eth2 via Multus and ipvlan.  

In this case, you would notice that VPC routing table gets updated with the ENI id of the eth1 and eth2 interface of the worker node (shown as ENI2 and ENI3) against the whole subnet CIDR as 10.10.254.0/28 and 10.10.254.16/28 and not as /32 addresses (pattern 1).

The vipmanager container does the route addition in this case, based on the Peers configured in the vipmanager-config configmap.

#### Multus Network Attachment - VPC Floating IP Addresses

In this case, we create multus network-interface-attachment definition using ipvlan and host-local (you can use other ipam as well). In the network attachment, for simplicity, we are just creating a range of 1 IP address. 

Note: In the multus net-attach-def, you dont need to define a route & gw. Actual routes are added by the vipmanager container, based on the peer configuration in vipmanager-config configmap(UseSBR or via Intf1Peers,Intf2Peers ). 

```
kubectl apply -f vpc/nad-1.yaml
kubectl apply -f vpc/nad-2.yaml
```

#### Floating IP (vipmanager) container deployment 
#### vipmanager as initContainer 

If you are deploying the vipmanager container as initContainer, ensure that your configmap (vipmanager-config) has below settings

```
  RunAsSidecar: "False"
  SubnetBasedLoopbacks: "True"
```
Add the vip manager container as initContainer (example below), in your deploymentset/statefulset. 

```
      initContainers:
      - name: vipmanager
        image: xxxxxxxxxxxx.dkr.ecr.us-east-2.amazonaws.com/vipmanager:0.1
        imagePullPolicy: IfNotPresent
        securityContext:
          capabilities:
            add: ["NET_ADMIN"]
        args: [/bin/sh, -c, '/app/script.sh']
        envFrom:
              - configMapRef:
                  name: vipmanager-config
```
you can also refer to sample deployment-initContainer.yaml in this git.

```
kubectl apply -f deployment-initContainer.yaml
```
#### vipmanager as sidecar container 
If you are deploying the vimpmanager container as sidecar container, ensure that your configmap (vipmanager-config) has below settings

```
  RunAsSidecar: "True"
  SubnetBasedLoopbacks: "True"
```
Add the vip manager container as an another container (example below), in your deploymentset/statefulset. 
```
      containers:
      - name: vipmanager
        image: xxxxxxxxxxxx.dkr.ecr.us-east-2.amazonaws.com/vipmanager:0.1
        imagePullPolicy: IfNotPresent
        securityContext:
          capabilities:
            add: ["NET_ADMIN"]
        args: [/bin/sh, -c, '/app/script.sh']
        envFrom:
              - configMapRef:
                  name: vipmanager-config
```
you can also refer to sample deployment-sidecar.yaml in this git.

```
kubectl apply -f deployment-sidecar.yaml
```

Once the containers are deployed you can test the ping or any other traffic (based on the secgroup rules) from one of the above configured peers. you can also check the routes in the vpc route tables, for the ENI route against the dummy subnet cidr.

## Cleanup

```
kubectl delete -f busybox-deployment-sidecar.yaml
kubectl delete -f busybox-deployment-initContainer.yaml
kubectl delete net-attach-def nad-if1 
kubectl delete net-attach-def nad-if2
kubectl delete cm vipmanager-config 
```
## License

This library is licensed under the MIT-0 License. See the LICENSE file.

