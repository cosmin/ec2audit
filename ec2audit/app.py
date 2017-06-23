from os import environ
from os.path import join
from boto import ec2, dynamodb

from ec2audit.utils import *
from ec2audit.output import to_dir, to_stdout

def name_and_tags(it):
    tags = it.tags.copy()
    name = tags.pop('Name', it.id)
    return name, NaturalOrderDict(tags)

def instance_data(i):
    data = NaturalOrderDict()

    data['zone'] = i.placement

    verbatim = ['id', 'image_id', 'architecture', 'instance_type',
                'key_name', 'launch_time',
                'root_device_type', 'state']

    vpc_only = ['sourceDestCheck', 'subnet_id', 'vpc_id']

    for key in verbatim:
        v = getattr(i, key)
        if v == '' or v == None: # but not False
            continue
        data[key] = v

    if i.__dict__.get('vpc_id'):
        for key in vpc_only:
            data[key] = i.__dict__[key]

    data['security_groups'] = sorted([g.name for g in i.groups])
    data['vpc_id'] = i.vpc_id
    data['subnet_id'] = i.subnet_id
    data['instance_type'] = i.instance_type

    if i.block_device_mapping:
        data['volumes'] = NaturalOrderDict()
        for dev, vol in i.block_device_mapping.items():
            data['volumes'][dev] = vol.volume_id

    if i.interfaces:
        data['interfaces'] = NaturalOrderDict()
        for nic in i.interfaces:
            ips = {}
            ips['privateIpAddresses'] = []
            if hasattr(nic, 'publicIp'):
                ips['publicIp'] = nic.publicIp
            for pi_attr in nic.private_ip_addresses:
                ips['privateIpAddresses'].append([pi_attr.private_ip_address, "Primary = %s" %(pi_attr.primary)])
            data['interfaces'][nic.id] = ips

    name, tags = name_and_tags(i)
    if tags:
        data['tags'] = tags

    if name != data['id']:
        data['name'] = name
        return name + '-' + data['id'], data
    else:
        return name, data

def get_ec2_instances(econ):
    instances = NaturalOrderDict()
    for res in econ.get_all_instances():
        for i in res.instances:
            name, data = instance_data(i)
            instances[name] = data

    return instances

def volume_data(vol):
    data = NaturalOrderDict()

    tags = vol.__dict__['tags']
    if tags:
        data['tags'] = NaturalOrderDict(tags)

    for key in ['id', 'create_time', 'size', 'status', 'snapshot_id']:
        data[key] = vol.__dict__[key]

    return vol.id, data

def instance_relevant_volume(vol):
    return NaturalOrderDict(id=vol['id'], size=vol['size'])

def get_ec2_volumes(econ):
    return NaturalOrderDict(volume_data(vol) for vol in econ.get_all_volumes())

def handle_rules(sg, rules):
    data = NaturalOrderDict()
    for rule in rules:
        proto = data.setdefault(rule.ip_protocol, NaturalOrderDict())
        if rule.to_port == '-1':
            port = '*'
        elif rule.from_port == rule.to_port:
            port = rule.from_port
        else:
            port = rule.from_port + "-" + rule.to_port
        fromto = proto.setdefault(port, [])

        for grant in rule.grants:
            if grant.cidr_ip:
                fromto.append(grant.cidr_ip)
            else:
                if grant.owner_id != sg.owner_id:
                    fromto.append(dict(name=(grant.owner_id, grant.group_id)))
                else:
                    fromto.append(grant.groupId)

    for proto, ports in data.items():
        for port in ports:
            ports[port] = sorted(ports[port])

    return data

def sg_data(sg):
    data = NaturalOrderDict()
    data['id'] = sg.id
    data['inbound'] = handle_rules(sg, sg.rules)
    if sg.rules_egress:
        data['outbound'] = handle_rules(sg, sg.rules_egress)
    data['name'] = sg.name
    data['vpc_id'] = sg.vpc_id
    return sg.name, data

def get_ec2_security_groups(ec2):
    return NaturalOrderDict(sg_data(sg) for sg in ec2.get_all_security_groups())

def run(params):
    access_key, secret_key = get_aws_credentials(params)
    region = params['<region>']
    if params['--format'] not in ['j', 'y', 'p', 'json', 'yaml', 'pprint', 'db']:
        exit_with_error('Error: format must be one of json or yaml\n')

    con = ec2.connect_to_region(region,
                                 aws_access_key_id=access_key,
                                 aws_secret_access_key=secret_key)


    volumes = get_ec2_volumes(con)
    instances = get_ec2_instances(con)
    security_groups = get_ec2_security_groups(con)

    for instance in instances.values():
        if 'volumes' in instance:
            for k, v in instance['volumes'].items():
                instance['volumes'][k] = instance_relevant_volume(volumes[v])

    output = params['--output']
    data = NaturalOrderDict(volumes=volumes,
                            instances=instances,
                            security_groups=security_groups)

    if not output:
        to_stdout(data, params['--format'])
    else:
        to_dir(data, params['--format'], output)
