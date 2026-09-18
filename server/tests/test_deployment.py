"""Static deployment contracts; no AWS access and no secret material."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class TemplateLoader(yaml.SafeLoader):
    pass


def tag(loader, suffix, node):
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_scalar(node)


TemplateLoader.add_multi_constructor("!", tag)


def test_public_ports_and_mount_safety():
    override = yaml.load((ROOT / "compose.aws.yml").read_text(), Loader=TemplateLoader)
    services = override["services"]
    assert services["nvr"]["ports"] == []
    assert services["nvr"]["environment"]["NVR_AUTH_MODE"] == "session"
    assert services["caddy"]["ports"] == ["80:80", "443:443"]
    for name in ["nvr-config", "mediamtx", "nvr", "caddy"]:
        for volume in services[name]["volumes"]:
            if isinstance(volume, dict):
                assert volume["bind"]["create_host_path"] is False
    dropin = (ROOT / "infra/aws/docker-mount.conf").read_text()
    assert "RequiresMountsFor=/srv/rasprec" in dropin
    assert "AssertPathIsMountPoint=/srv/rasprec" in dropin
    assert "mkfs" not in (ROOT / "infra/aws/bootstrap-host.sh").read_text()


def test_infrastructure_retains_encrypted_data_without_secrets():
    stack = yaml.load((ROOT / "infra/aws/stack.yml").read_text(), Loader=TemplateLoader)
    resources = stack["Resources"]
    data = resources["DataVolume"]
    assert data["DeletionPolicy"] == data["UpdateReplacePolicy"] == "Retain"
    assert data["Properties"]["Encrypted"] is True
    assert resources["Instance"]["Properties"]["MetadataOptions"]["HttpTokens"] == "required"
    ingress = resources["SecurityGroup"]["Properties"]["SecurityGroupIngress"]
    assert {rule["FromPort"] for rule in ingress} == {80, 443}
    assert not any("password" in name.lower() or "secret" in name.lower() for name in stack["Parameters"])
    assert "UserData" not in resources["Instance"]["Properties"]


def test_runtime_excluded_from_build_context():
    lines = (ROOT / ".dockerignore").read_text().splitlines()
    assert lines[0] == "**"
    assert {line for line in lines if line.startswith("!")} == {
        "!Dockerfile", "!requirements.txt", "!nvr/", "!nvr/**"
    }
