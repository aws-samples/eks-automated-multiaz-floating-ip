#!/bin/sh
cd /app
tag="ALL"
if [ -n "${VPCRTTag}" ]; then
    tag=${VPCRTTag}
fi
args=""
if [ -n "${Intf1Peers}" ]; then
    args+=" --intf1Peers "
    args+="${Intf1Peers}"
fi
if [ -n "${Intf2Peers}" ]; then
    args+=" --intf2Peers "
    args+="${Intf2Peers}"
fi
if [ -n "${RunAsSidecar}" ]; then
    args+=" --runAsSidecar "
    args+="${RunAsSidecar}"
fi
if [ -n "${UseSBR}" ]; then
    args+=" --useSBR "
    args+="${UseSBR}"
fi
if [ -n "${SubnetBasedLoopbacks}" ]; then
    args+=" --subnetLoopbacks "
    args+="${SubnetBasedLoopbacks}"
fi
echo option ${tag} ${args}
python3 -u assign-vip.py --vpcRTTag ${tag}  ${args}
