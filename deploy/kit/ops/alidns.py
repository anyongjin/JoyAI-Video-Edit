"""Manage one explicit hostname; credentials remain on the operator machine."""
import argparse
import base64
import datetime
import hashlib
import hmac
import json
import ipaddress
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid

from dotenv import dotenv_values


def encode(value):
    return urllib.parse.quote(str(value), safe="~")


def request(action, credentials, **parameters):
    parameters.update(
        Action=action, Version="2015-01-09", Format="JSON",
        AccessKeyId=credentials["OSS_ACCESS_KEY_ID"],
        SignatureMethod="HMAC-SHA1", SignatureVersion="1.0",
        SignatureNonce=str(uuid.uuid4()),
        Timestamp=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    canonical = "&".join(f"{encode(k)}={encode(v)}" for k, v in sorted(parameters.items()))
    signature = hmac.new(
        (credentials["OSS_ACCESS_KEY_SECRET"] + "&").encode(),
        ("GET&%2F&" + encode(canonical)).encode(), hashlib.sha1,
    ).digest()
    parameters["Signature"] = base64.b64encode(signature).decode()
    url = "https://alidns.aliyuncs.com/?" + urllib.parse.urlencode(parameters)
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        details = json.load(error)
        raise RuntimeError(f"AliDNS {details.get('Code')}: {details.get('Message')}") from None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default="/home/ctyun/nuva/outfit_agent/.env")
    parser.add_argument("--set-ip")
    parser.add_argument("--domain", default="joyai.nuvatech.cn")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]+", args.domain) or args.domain.count(".") < 2:
        parser.error("Use one subdomain such as joyai.nuvatech.cn")
    rr, domain = args.domain.split(".", 1)
    if args.set_ip:
        ipaddress.IPv4Address(args.set_ip)
    credentials = dotenv_values(args.env_file)
    for key in ("OSS_ACCESS_KEY_ID", "OSS_ACCESS_KEY_SECRET"):
        if not credentials.get(key):
            raise RuntimeError(f"Missing {key}")
    result = request("DescribeDomainRecords", credentials, DomainName=domain,
                     RRKeyWord=rr, SearchMode="EXACT", PageSize=500)
    records = [r for r in result["DomainRecords"]["Record"] if r["RR"] == rr]
    if args.set_ip:
        if len(records) > 1 or (records and records[0]["Type"] != "A"):
            raise RuntimeError("Conflicting DNS records; no records changed")
        if not records:
            request("AddDomainRecord", credentials, DomainName=domain, RR=rr,
                    Type="A", Value=args.set_ip, TTL=600)
        elif records[0]["Value"] != args.set_ip:
            request("UpdateDomainRecord", credentials, RecordId=records[0]["RecordId"],
                    RR=rr, Type="A", Value=args.set_ip, TTL=600)
        if records and records[0]["Status"] != "ENABLE":
            request("SetDomainRecordStatus", credentials, RecordId=records[0]["RecordId"], Status="Enable")
        result = request("DescribeDomainRecords", credentials, DomainName=domain,
                         RRKeyWord=rr, SearchMode="EXACT", PageSize=500)
        records = [r for r in result["DomainRecords"]["Record"] if r["RR"] == rr]
    fields = ("RecordId", "RR", "Type", "Value", "Status", "TTL")
    print(json.dumps([{k: r.get(k) for k in fields} for r in records], indent=2))


if __name__ == "__main__":
    assert encode("a b+c/") == "a%20b%2Bc%2F"
    main()
