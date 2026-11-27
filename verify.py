#!/usr/bin/env python3
"""Read-only verification for the ITT633 site-to-site IPsec VPN.

This script does NOT configure or repair routers. It connects over SSH,
runs show/ping commands, and returns individual PASS/FAIL/ERROR results.
"""
from __future__ import annotations

import argparse
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml
from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException


def result(device, test_id, description, expected, actual, status, duration, evidence):
    return {
        "device": device,
        "test_id": test_id,
        "description": description,
        "expected": expected,
        "actual": actual,
        "status": status,
        "duration_seconds": round(duration, 3),
        "evidence_path": evidence,
    }


def run_command(connection, command, delay=1.0):
    """Run a read-only IOS XE command and return its output."""
    return connection.send_command(
        command,
        read_timeout=30,
        cmd_verify=False,
        strip_prompt=True,
        strip_command=True,
    )


def save_evidence(run_dir: Path, device_name: str, test_id: str, output: str) -> str:
    safe_device = re.sub(r"[^A-Za-z0-9_.-]", "_", device_name)
    path = run_dir / f"{safe_device}_{test_id}.txt"
    path.write_text(output or "(empty output)\n", encoding="utf-8")
    return str(path)


def check_contains(output: str, required_terms: list[str]) -> tuple[bool, str]:
    missing = [term for term in required_terms if term.lower() not in output.lower()]
    if missing:
        return False, "Missing expected configuration text: " + ", ".join(missing)
    return True, "All configured text checks were found."




def route_check(
    output: str,
    expected_network: str,
    expected_tunnel_ip: str
) -> tuple[bool, str]:

    lowered = output.lower()

    bad = [
        "% network not in table",
        "not in table",
        "subnet not in table",
        "% invalid",
        "invalid input"
    ]

    if any(x in lowered for x in bad):
        return False, "Remote LAN route was not found."

    network_found = expected_network.lower() in lowered
    next_hop_found = expected_tunnel_ip in output
    tunnel_found = "tunnel0" in lowered

    if network_found and next_hop_found and tunnel_found:
        return True, (
            f"Route to {expected_network} confirmed via "
            f"{expected_tunnel_ip} through Tunnel0."
        )

    return False, (
        f"Route verification failed. "
        f"Network found={network_found}, "
        f"next hop found={next_hop_found}, "
        f"Tunnel0 found={tunnel_found}."
    )




def ike_check(output: str, expected_peer: str) -> tuple[bool, str]:
    peer_pattern = (
        rf"\bRemote:\s*{re.escape(expected_peer)}(?:/500)?\b"
    )

    peer_found = bool(
        re.search(peer_pattern, output, re.I)
    )

    state_found = bool(
        re.search(r"\bStatus:\s*READY\b", output, re.I)
    )

    if peer_found and state_found:
        return True, (
            f"Expected remote peer {expected_peer} found "
            "and IKEv2 status is READY."
        )

    return False, (
        f"IKEv2 not confirmed. "
        f"Remote peer found={peer_found}; "
        f"READY status found={state_found}."
    )



def parse_ipsec_counters(output: str) -> tuple[int | None, int | None]:
    """Return aggregate encapsulated and decapsulated packet counts."""

    enc_values = re.findall(r"#pkts\s+encaps:\s*(\d+)", output, re.I)
    dec_values = re.findall(r"#pkts\s+decaps:\s*(\d+)", output, re.I)

    if not enc_values or not dec_values:
        return None, None

    enc_count = sum(map(int, enc_values))
    dec_count = sum(map(int, dec_values))

    return enc_count, dec_count




def ping_check(output: str) -> tuple[bool, str]:
    match = re.search(r"Success rate is\s+(\d+)\s+percent", output, re.I)

    if match:
        rate = int(match.group(1))
        if rate == 100:
            return True, f"Ping successful: {rate}%."
        return False, f"Ping failed: only {rate}% success. Expected 100%."

    return False, "Ping output was not recognised or no success rate was found."



def verify_device(device_name: str, device_cfg: dict, run_dir: Path, settings: dict,
                  run_id: str) -> list[dict]:
    results = []
    host = device_cfg["host"]
    password_env = device_cfg.get("password_env", "VPN_ROUTER_PASSWORD")
    password = os.getenv(password_env)
    username = device_cfg.get("username", settings.get("username", "automation"))

    if not password:
        return [result(device_name, "CONN01", "SSH connection", "Password from environment variable",
                       f"Environment variable {password_env} is not set", "ERROR", 0, "")]

    params = {
        "device_type": device_cfg.get("device_type", "cisco_xe"),
        "host": host,
        "username": username,
        "password": password,
        "port": int(device_cfg.get("port", 22)),
        "timeout": int(settings.get("ssh_timeout_seconds", 10)),
        "conn_timeout": int(settings.get("ssh_timeout_seconds", 10)),
        "fast_cli": False,
    }

    start = time.perf_counter()
    try:
        connection = ConnectHandler(**params)
        duration = time.perf_counter() - start
        results.append(result(device_name, "CONN01", "SSH connection",
                              "SSH login successful", f"Connected to {host}", "PASS",
                              duration, ""))
    except (NetmikoAuthenticationException, NetmikoTimeoutException, OSError) as exc:
        duration = time.perf_counter() - start
        results.append(result(device_name, "CONN01", "SSH connection",
                              "SSH login successful", str(exc), "FAIL", duration, ""))
        return results

    try:
        # 1) Inspect relevant configuration. Exact required strings are configurable
        # after Syahmi supplies the proven running-config output.
       
        commands = {
            "CFG01": "show running-config | section interface Tunnel0",
            "CFG02": "show running-config | section crypto",
            "CFG03": "show running-config | include ^ip route",
        }

        config_outputs = {}

        for test_id, command in commands.items():
            t0 = time.perf_counter()
            output = run_command(connection, command)
            config_outputs[test_id] = output

            if test_id == "CFG01":
                expected_values = [
                    "interface Tunnel0",
                    f"ip address {device_cfg['tunnel_ip']} 255.255.255.252",
                    "tunnel source GigabitEthernet2",
                    "tunnel mode ipsec ipv4",
                    f"tunnel destination {device_cfg['peer_wan_ip']}",
                    "tunnel protection ipsec profile NUSALINK-IPSEC",
                ]

                missing = [
                    value for value in expected_values
                    if value.lower() not in output.lower()
                ]

                passed = bool(output.strip()) and not missing
                expected = "All expected Tunnel0 configuration values are present"
                actual = (
                    "All Tunnel0 settings confirmed"
                    if passed else f"Missing settings: {', '.join(missing)}"
                )

            
            elif test_id == "CFG02":
                expected_values = [
                    "crypto ikev2 proposal NUSALINK-PROPOSAL",
                    "encryption aes-cbc-256",
                    "integrity sha256",
                    "group 14",
                    "crypto ikev2 profile NUSALINK-IKEV2",
                    "crypto ipsec transform-set NUSALINK-TRANSFORM",
                    "esp-aes 256",
                    "esp-sha256-hmac",
                    "crypto ipsec profile NUSALINK-IPSEC",
                    "set transform-set NUSALINK-TRANSFORM",
                    "set ikev2-profile NUSALINK-IKEV2",
                ]

                missing = [
                    value for value in expected_values
                    if value.lower() not in output.lower()
                ]

                passed = bool(output.strip()) and not missing
                expected = "All expected IKEv2 and IPsec configuration values are present"
                actual = (
                    "All crypto settings confirmed"
                    if passed else f"Missing settings: {', '.join(missing)}"
                )
            
            else:
                remote_network = device_cfg["remote_lan_network"].split("/")[0]
                remote_tunnel_ip = device_cfg["remote_tunnel_ip"]

                expected_route = (
                    f"ip route {remote_network} "
                    f"255.255.255.0 Tunnel0 {remote_tunnel_ip}"
                )

                passed = expected_route.lower() in output.lower()

                expected = expected_route
                actual = (
                    "Expected static route confirmed"
                    if passed else
                    f"Expected route not found: {expected_route}"
                )

            safe_output= re.sub(
                r"(?im)^(\s*(?:pre-shared-key|password|secret)\s+).*$",
                r"\1<REDACTED>",
                output
            )

            evidence = save_evidence(
                run_dir, device_name, test_id, safe_output
            )

            results.append(result(
                device_name, test_id, command,
                expected, actual,
                "PASS" if passed else "FAIL",
                time.perf_counter() - t0, evidence
            ))

        # 2) Remote LAN route
        remote_lan = device_cfg["remote_lan_network"]
        t0 = time.perf_counter()
        route_output = run_command(connection, f"show ip route {remote_lan}")
        evidence = save_evidence(run_dir, device_name, "ROUTE01", route_output)
        passed, actual = route_check(
            route_output,
            remote_lan,
            device_cfg["remote_tunnel_ip"]
        )
        results.append(result(device_name, "ROUTE01", "Remote LAN route",
                              f"Route to {remote_lan} exists", actual,
                              "PASS" if passed else "FAIL", time.perf_counter() - t0, evidence))

        
        # 3) Wait up to configured limit for IKEv2 establishment
        peer = device_cfg["peer_wan_ip"]
        wait_limit = int(settings.get("ike_wait_seconds", 60))
        poll_interval = int(settings.get("poll_interval_seconds", 5))

        t0 = time.perf_counter()
        deadline = time.monotonic() + wait_limit
        ike_output = ""
        ike_pass = False

        while True:
            ike_output = run_command(
                connection, "show crypto ikev2 sa"
            )

            ike_pass, _ = ike_check(ike_output, peer)

            if ike_pass:
                break

            remaining = deadline - time.monotonic()

            if remaining <= 0:
                break

            time.sleep(min(poll_interval, remaining))

        ike_duration = time.perf_counter() - t0

        evidence = save_evidence(
            run_dir, device_name, "IKE01", ike_output
        )

        _, ike_actual = ike_check(ike_output, peer)

        results.append(result(
            device_name, "IKE01", "IKEv2 security association",
            f"Peer {peer} in established/READY state",
            ike_actual,
            "PASS" if ike_pass else "FAIL",
            ike_duration,
            evidence
        ))


        # 4) Verify IPsec SA, peer, status and packet counters.
        t0 = time.perf_counter()
        ipsec_before = run_command(connection, "show crypto ipsec sa")
        evidence = save_evidence(
            run_dir, device_name, "IPSEC01_before", ipsec_before
        )

        enc_before, dec_before = parse_ipsec_counters(ipsec_before)

        expected_peer = device_cfg["peer_wan_ip"]

        peer_found = bool(re.search(
            rf"\bcurrent_peer\s+{re.escape(expected_peer)}\b",
            ipsec_before,
            re.I
        ))

        active_statuses = re.findall(
            r"Status:\s*ACTIVE\(ACTIVE\)",
            ipsec_before,
            re.I
        )

        both_sas_active = len(active_statuses) >= 2

        counters_present = (
            enc_before is not None and dec_before is not None
        )

        ipsec_present = (
            peer_found and both_sas_active and counters_present
        )

        actual = (
            f"Peer found={peer_found}; "
            f"both SAs active={both_sas_active}; "
            f"encaps={enc_before}; decaps={dec_before}"
        )

        results.append(result(
            device_name, "IPSEC01", "IPsec security association",
            "Expected peer, active inbound/outbound SAs and readable counters",
            actual,
            "PASS" if ipsec_present else "FAIL",
            time.perf_counter() - t0,
            evidence
        ))


        # 5) Generate traffic using the local LAN interface address as source.
        source = device_cfg["lan_ip"]
        destination = device_cfg["remote_lan_gateway"]
        t0 = time.perf_counter()
        ping_output = run_command(connection, f"ping {destination} source {source} repeat 5 timeout 2")
        evidence = save_evidence(run_dir, device_name, "PING01", ping_output)
        ping_pass, ping_actual = ping_check(ping_output)
        results.append(result(device_name, "PING01", "LAN-source router ping",
                              f"Ping {destination} sourced from {source}", ping_actual,
                              "PASS" if ping_pass else "FAIL", time.perf_counter() - t0, evidence))

        # 6) Compare IPsec counters after traffic. Require an increase in both directions.
        t0 = time.perf_counter()
        ipsec_after = run_command(connection, "show crypto ipsec sa")
        evidence = save_evidence(run_dir, device_name, "IPSEC02_after", ipsec_after)
        enc_after, dec_after = parse_ipsec_counters(ipsec_after)
        counters_increased = (
            ping_pass
            and enc_before is not None and dec_before is not None
            and enc_after is not None and dec_after is not None
            and enc_after > enc_before
            and dec_after > dec_before
        )

        actual = f"before(enc={enc_before}, dec={dec_before}); after(enc={enc_after}, dec={dec_after})"
        results.append(result(device_name, "IPSEC02", "IPsec traffic counters",
                              "Both IPsec encapsulation and decapsulation counters increase after test traffic",
                              actual, "PASS" if counters_increased else "FAIL",
                              time.perf_counter() - t0, evidence))

    except Exception as exc:
        results.append(result(device_name, "RUNERR", "Verification execution",
                              "All verification commands complete", repr(exc),
                              "ERROR", 0, ""))
    finally:
        connection.disconnect()

    return results


def main():
    parser = argparse.ArgumentParser(description="Read-only IPsec VPN verification")
    parser.add_argument("--inventory", default="inventory.yaml")
    parser.add_argument("--output-dir", default="verification_results")
    args = parser.parse_args()

    inventory = yaml.safe_load(Path(args.inventory).read_text(encoding="utf-8"))
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    run_dir = Path(args.output_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    all_results = []
    for device_name, device_cfg in inventory["devices"].items():
        print(f"\nVerifying {device_name} ({device_cfg['host']}) ...")
        device_results = verify_device(device_name, device_cfg, run_dir,
                                       inventory.get("settings", {}), run_id)
        all_results.extend(device_results)
        for item in device_results:
            print(f"  {item['test_id']}: {item['status']} - {item['description']}")

    from report_results import write_reports
    overall = "PASS" if all(x["status"] == "PASS" for x in all_results) else "FAIL"
    write_reports(all_results, run_id, overall, run_dir)
    print(f"\nOverall result: {overall}")
    print(f"Evidence and reports saved in: {run_dir}")


if __name__ == "__main__":
    main()
