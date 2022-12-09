#!/usr/bin/env python3
# -----------------------------------------------------------
#// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
#// SPDX-License-Identifier: MIT-0
# This code demonstrates how to automate the VIP routing for a multus based container
# for multus pods
# author: Raghvendra Singh
# -----------------------------------------------------------
import requests
import botocore, boto3, json
import sys, datetime,argparse
import ipaddress

from requests.packages.urllib3 import Retry
import subprocess,copy,time
from collections import defaultdict
from multiprocessing import Process

## Logs are printed with timestamp as an output for kubectl logs of this container 
def tprint(var):
    print (datetime.datetime.now(),"-",var)
    
## This function gets the metadata token
def get_metadata_token():
    token_url="http://169.254.169.254/latest/api/token"
    headers = {'X-aws-ec2-metadata-token-ttl-seconds': '21600'}
    r= requests.put(token_url,headers=headers,timeout=(2, 5))
    return r.text

def getInstanceData(instanceData,vpcId):
    instance_identity_url = "http://169.254.169.254/latest/dynamic/instance-identity/document"
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=0.3)
    metadata_adapter = requests.adapters.HTTPAdapter(max_retries=retries)
    session.mount("http://169.254.169.254/", metadata_adapter)
    headers={}
    try:
        r = requests.get(instance_identity_url, timeout=(2, 5))
        code=r.status_code
        if code == 401: ###This node has IMDSv2 enabled, hence unauthorzied, we need to get token first and use the token
            tprint("node has IMDSv2 enabled!! Fetching Token first")
            token=get_metadata_token()
            headers = {'X-aws-ec2-metadata-token': token}
            r = requests.get(instance_identity_url, headers=headers, timeout=(2, 5))
            code=r.status_code
        if code == 200:
            response_json = r.json()
            instanceId = response_json.get("instanceId")
            region = response_json.get("region")
            instanceData["instanceId"]=instanceId
            instanceData["region"]=region
            macs_url="http://169.254.169.254/latest/meta-data/network/interfaces/macs/"
            r = requests.get(macs_url, headers=headers, timeout=(2,5))
            code=r.status_code            
            if code == 200:
                response = r.text
                macs=r.text.splitlines()
                for m in macs:
                    mac=m.rstrip('/')
                    eniData={}
                    #interfaceKeys=["interface-id","vpc-id","subnet-ipv4-cidr-block","subnet-id","device-number"]
                    interfaceKeys=["interface-id","vpc-id","subnet-ipv4-cidr-block","device-number"]
                    for key in interfaceKeys:
                        r = requests.get(macs_url+m+key, headers=headers, timeout=(2,5))
                        code=r.status_code            
                        if code == 200:     
                            eniData[key] = r.text        
                        if key == "vpc-id":
                            vpc=r.text 
                            if vpc not in vpcId:
                                vpcId.append(vpc)                                                                       
                    instanceData[mac]=eniData
            else:
                tprint("Got error while executing " + macs_url)            
    except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError) as err:
        tprint("Execption: Connection to AWS EC2 Metadata timed out: " + str(err.__class__.__name__))
        tprint("Execption: Is this an EC2 instance? Is the AWS metadata endpoint blocked? (http://169.254.169.254/)")
        raise
    except Exception as e:
        tprint("Execption: caught exception " + str(e.__class__.__name__))
        raise             
def build_subnet_data(client,vpcId,subnetDetails) :
    try:
        response = client.describe_subnets(
            Filters=[
                {
                    'Name': 'vpc-id','Values':  vpcId
                },]
        )
        for i in response['Subnets']:
            x={}
            x['SubnetId']=i['SubnetId']     
            subnetDetails[i['CidrBlock']]=x
    except Exception as e:
        tprint("Execption: caught exception " + str(e.__class__.__name__))
        raise                      
## This function runs the shell command and returns the command output
def shell_run_cmd(cmd,printOutput=True):
    if printOutput:
        tprint(cmd)
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,encoding="utf-8")
    stdout, stderr = p.communicate()
    retCode = p.returncode
    stdout= stdout.strip()
    if printOutput:
        tprint("stdout:" + str(stdout.split("\n")) + " stderr: " + stderr + " retCode: " + str(retCode))
    return (stdout, retCode)      
def addRoute(peers,gw,intf):
    for peer in peers:
        cmd="ip r add " + peer + " via "+ gw + " dev " + intf+ " onlink"
        shell_run_cmd(cmd)
# This function checks if the prefix with prefixlen(netmask) belongs to a subnet or not, and returns the subnet cidr the ip address belongs to
def find_subnet_cidr(inputCidr,subnetDetails):
    foundCidr=None
    data=inputCidr.split('/')
    prefix=data[0]
    prefixLen=int(data[1])
    if  prefixLen == 32:        
        for cidr in subnetDetails.keys():
            for addr in ipaddress.IPv4Network(cidr):
                ##if its a subnet CIDR then overwrite with subnet CIDR
                if ipaddress.IPv4Address(prefix) == addr:                       
                    foundCidr=cidr
                    break
            if foundCidr:
                break     
    else:
        if inputCidr in subnetDetails.keys():
            foundCidr=inputCidr      
    tprint("inputCidr:"+ inputCidr + " subnet Cidr found:"+ str(foundCidr))   
    return foundCidr     
def add_route_parallel(eni,cidr,rtbResources):
    procipv4 = []
    start = time.perf_counter() 
    for rtb in rtbResources:
        tprint(f"processing rtb: {rtb} for { cidr} {eni} ")
        p = Process(target=add_route_new, args=(eni,cidr,rtb,rtbResources[rtb]))
        p.start()
        procipv4.append(p)                    
    # wait for  the parallel requests to complete execution and return 
    for p in procipv4:
        p.join(10)    
    end = time.perf_counter()        
    tprint(f"Finished All route tables for { cidr} {eni}  route Time is {end - start}")
def add_route_new(eni,cidr,rtb,ec2client) :   
    #start = time.perf_counter() 
        ##try replacing first adding
    action="replace"
    try:   
        route = ec2client.Route(rtb,cidr)
        response = route.replace(
                NetworkInterfaceId=eni,
                )        
#        logger1.debug("added route for",cidr)
    except botocore.exceptions.ClientError as err:
        errorcode = err.response['Error']['Code']
        tprint(f"error { errorcode } for { cidr} {eni} in {rtb} while replace will perfrom add" )       
        action="add"
        try: 
            route_table=ec2client.RouteTable(rtb)
            route = route_table.create_route(
                DestinationCidrBlock=cidr,
                NetworkInterfaceId=eni
            )        
        except botocore.exceptions.ClientError as err:
            errorcode = err.response['Error']['Code']            
            tprint(f"error { errorcode } for { cidr} {eni} in {rtb} while  adding, route might still be there, skipping add" )

## This function collects the details of VPC Route Tables in the given vpcs
def build_vpc_rt_data(ec2_client,vpcId,rtbResources,vpcRTTag) :
    filters=None
    try:
        if vpcRTTag == "ALL" :
            filters=[
                {
                    'Name': 'vpc-id','Values':  vpcId
                },]  
        else:
            data=vpcRTTag.split('=')
            if len(data) > 1:
                filters=[
                    {
                        'Name': 'vpc-id','Values':  vpcId
                    },
                    {
                        'Name': 'tag:'+data[0],'Values':  [data[1]]
                    }] 
            else:
                filters=[
                    {
                        'Name': 'vpc-id','Values':  vpcId
                    },
                    {
                        'Name': 'tag-key','Values':  [data[0]]
                    }]                         
        response = ec2_client.describe_route_tables(
            Filters=filters
        )
        for i in response['RouteTables']:
            if i['RouteTableId'] not in rtbResources.keys():
                tprint("Adding " + i['RouteTableId'] + " in pool of VPC RTs")
                rtbResources[i['RouteTableId']]=None
    except Exception as e:
        tprint("Execption: caught exception " + str(e.__class__.__name__))
        raise     
def main():    
    instance_id = None
    cmd = "for x in `ls /sys/class/net/ | egrep -v 'eth0|lo'`; do y=`ip a show dev $x | egrep 'link/ether'|cut -d ' ' -f 6`;z=`ip a show dev $x | egrep 'scope global'|cut -d ' ' -f 6`; echo ${x}=${y}=${z}; done"
    region= None
    instanceData = {}
    initcontainer=True
    interfaceDetails={}
    curInterfaceDetails = {}
    vpcId=[]
    rtbResources={}
    subnetLoopbacks=False 
    subnetDetails={}
    peers={}
    routeAddCompleted=False
    parser = argparse.ArgumentParser(
            description='VIP script to add entries in VPC RT'
           )
    parser.add_argument('--vpcRTTag', metavar='vpcRTouteTableTags', default='ALL',required=False, help='Options: ALL|BGPSpeaker=yes. This represents any tags as key=value pair to identify VPC route Tables,ex: BGPSpeaker=yes . ALL (or if no value provided) means select all route tables')
    parser.add_argument('--intf1Peers', metavar='eth1Peers', default=None,required=False, help='Options: 1.1.1.1,1.1.1.2 This represents This optional param is to add static route comma separated peers for first Multus interface i.e. eth1')
    parser.add_argument('--intf2Peers', metavar='eth2Peers', default=None,required=False, help='Options: 2.1.1.1,2.1.1.2 This represents This optional param is to add static route comma separated peers for second Multus interface i.e. eth2')
    parser.add_argument('--subnetLoopbacks', metavar='SubnetBasedLoopbacks', default="False",required=False, help='true|false This indicates if Loopbacks are defined as subnets in VPC. Default value is false')    
    parser.add_argument('--runAsSidecar', metavar='runAsSidecar', default="False",required=False, help='true|false This indicates if this container shall run as sidecar, true mean sidecar, false means initContainer Default value is false')    

    args = parser.parse_args()
    vpcRTTag=args.vpcRTTag
    if args.subnetLoopbacks == "True" or args.subnetLoopbacks == "true" :
        subnetLoopbacks=True    
    if args.runAsSidecar == "True" or args.runAsSidecar == "true" :
        initcontainer=False ## run as Sidecar      
        tprint("Running as Sidecar container")      
    else:
        tprint("Running as initContainer")      
    if args.intf1Peers:
        peers['1']=args.intf1Peers.split(',')
    if args.intf2Peers:
        peers['2']=args.intf2Peers.split(',')


    while (1) :
        retCode=0
        
        if not instance_id :
            try:
                # at the very first iteration, get the instance ID of the underlying worker & create a temp boto3 client to get instance data attached ENIs and corresponding subnet IP CIDRblocks 
                    getInstanceData(instanceData,vpcId)
                    instance_id = instanceData["instanceId"]
                    region = instanceData["region"]
                    tprint(instanceData)
                    tprint(vpcId)
                    #tprint ("Got InstanceId: " + instance_id + " region: " + region)  
                    ec2_client = boto3.client('ec2', region_name=region)
                    build_vpc_rt_data(ec2_client,vpcId,rtbResources,vpcRTTag)
                    tprint(rtbResources)
                    if len (rtbResources.keys()) <= 0:
                        raise ValueError("No VPC Route table found for vpcRTTag: " + vpcRTTag)     
                    if subnetLoopbacks:
                        build_subnet_data(ec2_client,vpcId,subnetDetails)
                        tprint(subnetDetails)                    
                    #Tn this coode, we are planning to do parallel processing and same client cant be used parallely for multiple parallel requests, so we are creating a Map/dictionary of ec2 clients for each ENI/subnet CIDR attached to the worker 
                    # These clients are stored as values against the dictionary where subnet RT Id is the key
                    for rtb in rtbResources:
                        if rtbResources[rtb] == None:
                            tprint("Adding EC2 boto3 resource for RT: "+ rtb)
                            rtbResources[rtb]= boto3.resource('ec2',region_name=region)                                
            # If these are any exceptions in getting the worker node details,  catch it using catch all exception and keep trying & logging untill the problem is resolved
            except (Exception) as e:
                tprint ("Exception in getting the details :" + str(e))     
                tprint ("Retrying!!")
                time.sleep(1)
                instance_id=None ### override the instanceid to go through over it again
                continue
        try:            
            #Run the shell command on the pod which will get the list of multus secondary interfaces MAC and Ips (non eth0)    
            ##get MAC and IP output from pod for multus i/f
            output, retCode = shell_run_cmd(cmd,False)
            if retCode == 0 :
                newIPList = output.splitlines()
                for interface in newIPList:
                    ## Ex: aa:bb:cc:dd:ee:ff=192.168.192.17/30
                    data=interface.split('=')
                    ## store MAC as key and IP as value, ignore netmask and add as /32
                    intfName=data[0]
                    mac=data[1]
                    ipList=[]
                    for cidr in data[2].split(' '):
                        ip=cidr.split('/')[0] 
                        prefix='/32'
                        ipList.append(ip+prefix)
                    interfaceDetails[mac]={"intfName": intfName, "ipList": ipList}
                ##if there add been any change in the IPs on the pod then add the new routes in VPC
                if curInterfaceDetails != interfaceDetails:
                    tprint(interfaceDetails)
                    #if there are IPs allocated on the pod,
                    if len(interfaceDetails.keys()) > 0 :
                        for mac in interfaceDetails:
                            ipList=interfaceDetails[mac]["ipList"]
                            intfName=interfaceDetails[mac]["intfName"]
                            for ip in ipList:
                                if subnetLoopbacks:
                                    cidr=find_subnet_cidr(ip,subnetDetails)    
                                else:
                                    cidr=ip         
                                #Add VPC RT entries in the relevant tables             
                                add_route_parallel(instanceData[mac]["interface-id"],cidr,rtbResources)      
                            if  routeAddCompleted == False:
                                ##Add static route for the peers only once
                                ethIndex=instanceData[mac]["device-number"]
                                ipNet =ipaddress.ip_network(instanceData[mac]["subnet-ipv4-cidr-block"], strict=False)
                                gw = str(ipNet[1])    
                                if ethIndex in peers.keys():
                                    tprint("add route for " + str(peers[ethIndex]))                                              
                                    addRoute(peers[ethIndex],gw,intfName)     
                                else:
                                    tprint ("No peers present for device-index "+ ethIndex+ ",skipping route add for " + intfName )                                      
                        if initcontainer == True :
                            tprint ("Started as initContainer. Exiting after successful execution")  
                            exit(0)        
                        # Once all the ipv4 and ipv6 assignments are completed, then copy the newIp list as current List 
                        curInterfaceDetails = copy.deepcopy(interfaceDetails)  
                        routeAddCompleted=True
                    else:
                        tprint ("No IPs present in system for cmd: "+ cmd )                                        
            else:
                tprint ("Error received: " + retCode + " for command: "+ cmd )
        # If these are any exceptions in ip assignment to the NICs then catch it using catch all exception and keep trying & logging untill the problem is resolved
        except (Exception) as e:
            tprint ("Exception in Route Assignment to RT:" + str(e))     
            tprint ("continuing the handling")
        time.sleep(0.5)

##Main Usage <scriptName> 

if __name__ == "__main__":
    main()
