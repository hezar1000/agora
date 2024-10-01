#!/bin/bash
# Instructions for installing on Ubuntu
set -e

export pip=pip3
export python=python3

# TODO: you should use your domain name here if you have one
export server_address=`curl ipecho.net/plain`

dbname="mta_db"
dbuser="mta"
# TODO: you should choose a password before continuing
export dbpass=""
export linuxuser="mta"


install_postgresql() {
    sudo update-rc.d postgresql enable

    sudo systemctl enable postgresql.service
    sudo systemctl start postgresql.service

    sudo su - postgres -c "echo \"CREATE USER $dbuser PASSWORD '$dbpass';
    create database $dbname encoding 'utf-8';
    grant all privileges on database $dbname to $dbuser;\" | psql"

    sudo systemctl restart postgresql.service
}

sudo dpkg-reconfigure -f noninteractive debconf

sudo DEBIAN_FRONTEND=noninteractive apt-get -y update
sudo DEBIAN_FRONTEND=noninteractive apt-get -qq -y upgrade

sudo DEBIAN_FRONTEND=noninteractive apt install -y vim zsh mlocate htop build-essential
sudo DEBIAN_FRONTEND=noninteractive apt install -y libxml2-dev libxslt1-dev uwsgi uwsgi-plugin-python3
sudo DEBIAN_FRONTEND=noninteractive apt install -y nginx postgresql postgresql-contrib
sudo DEBIAN_FRONTEND=noninteractive apt install -y python3 python3-pip

install_postgresql;

sudo updatedb

sudo useradd -m ${linuxuser}
sudo usermod -a -G ${linuxuser} $(whoami)  # this is not really needed
sudo chmod g+w -R /home/${linuxuser}

sudo tee >> /etc/sudoers <<EOF
${linuxuser} ALL=(ALL) NOPASSWD:ALL
EOF

# login as mta
cp install_user.sh /home/mta
cp -r ~/.ssh /home/${linuxuser}
sudo chown -R ${linuxuser}:${linuxuser} /home/${linuxuser}/
sudo chmod go-w /home/${linuxuser}/

sudo -E su -c '/home/${linuxuser}/install_user.sh' ${linuxuser}