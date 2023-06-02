FROM public.ecr.aws/amazonlinux/amazonlinux:2
WORKDIR /app/
RUN yum -y update \
    &&  yum -y install python3 iproute iproute2 iputils tcpdump curl net-tools procps sudo wget
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install  -r /tmp/requirements.txt
COPY assign-vip.py assign-vip.py
copy script.sh script.sh
RUN chmod 755 /app/script.sh
