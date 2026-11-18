#!/bin/bash
# EC2 user-data for the sidechannel-exp runner (Ubuntu 24.04, x86_64). Idempotent.
# Lessons baked in: Docker address pools kept away from the VPC (172.31.0.0/16) and from trial
# subnets (10.200-249.x); larger conntrack table (agents port-scan peers); no unattended reboots.
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive

mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'JSON'
{"bip": "192.168.240.1/24",
 "default-address-pools": [{"base": "192.168.0.0/17", "size": 24}],
 "log-driver": "json-file", "log-opts": {"max-size": "50m", "max-file": "3"}}
JSON

apt-get update
apt-get install -y ca-certificates curl gnupg rsync unzip jq python3 tcpdump
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
usermod -aG docker ubuntu
systemctl enable --now docker

# AWS CLI v2 (host only; containers can't reach IMDS: hop limit 1)
if ! command -v aws >/dev/null; then
  curl -fsSL https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o /tmp/awscliv2.zip
  unzip -q /tmp/awscliv2.zip -d /tmp && /tmp/aws/install && rm -rf /tmp/aws /tmp/awscliv2.zip
fi

echo nf_conntrack > /etc/modules-load.d/sc.conf
modprobe nf_conntrack || true
cat > /etc/sysctl.d/90-sc.conf <<'SYS'
net.netfilter.nf_conntrack_max = 1048576
net.netfilter.nf_conntrack_tcp_timeout_syn_sent = 30
SYS
sysctl --system >/dev/null || true

cat > /etc/apt/apt.conf.d/52sc-no-reboot <<'APT'
Unattended-Upgrade::Automatic-Reboot "false";
APT

# install -d only owns the last path component, so create each level as ubuntu
for d in /home/ubuntu/sidechannel-exp /home/ubuntu/.config /home/ubuntu/.config/sidechannel /home/ubuntu/.config/sidechannel/keys; do
  install -d -o ubuntu -g ubuntu "$d"
done
chmod 700 /home/ubuntu/.config/sidechannel /home/ubuntu/.config/sidechannel/keys
touch /var/lib/sc-bootstrap-done
