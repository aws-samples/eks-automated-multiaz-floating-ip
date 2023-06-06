FROM public.ecr.aws/amazonlinux/amazonlinux:latest
WORKDIR /app/
RUN yum -y update \
    &&  yum -y install python3 iproute iputils tcpdump net-tools procps sudo wget python3-pip
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install  -r /tmp/requirements.txt
COPY assign-vip.py assign-vip.py
copy script.sh script.sh
RUN chmod 755 /app/script.sh
