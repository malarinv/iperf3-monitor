import os
import sys
import unittest
import json
from unittest.mock import patch, MagicMock

import importlib.util

spec = importlib.util.spec_from_file_location("exporter_mod", os.path.join(os.path.dirname(__file__), "exporter.py"))
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)

class TestExporter(unittest.TestCase):

    def setUp(self):
        self.source = "node-a"
        self.dest = "node-b"
        self.labels = {'source_node': self.source, 'destination_node': self.dest, 'protocol': 'tcp'}
        self.udp_labels = {'source_node': self.source, 'destination_node': self.dest, 'protocol': 'udp'}

    def test_parse_tcp_success(self):
        tcp_json = {
            "end": {
                "streams": [
                    {
                        "sender": {
                            "mean_rtt": 250,  # 250 microseconds = 0.25 ms
                            "retransmits": 3
                        }
                    }
                ],
                "sum_sent": {
                    "seconds": 5.0,
                    "bytes": 50000000,
                    "bits_per_second": 80000000.0,
                    "retransmits": 3
                },
                "sum_received": {
                    "seconds": 5.0,
                    "bytes": 49000000,
                    "bits_per_second": 78400000.0
                }
            }
        }

        exporter.parse_and_publish_metrics(tcp_json, self.source, self.dest, 'tcp')

        self.assertEqual(exporter.IPERF_TEST_SUCCESS.labels(**self.labels)._value.get(), 1)
        self.assertAlmostEqual(exporter.IPERF_BANDWIDTH_MBPS.labels(**self.labels)._value.get(), 78.4)
        self.assertEqual(exporter.IPERF_TCP_RETRANSMITS.labels(**self.labels)._value.get(), 3)
        self.assertAlmostEqual(exporter.IPERF_TCP_RTT_MS.labels(**self.labels)._value.get(), 0.25)
        self.assertEqual(exporter.IPERF_TEST_DURATION_SECONDS.labels(**self.labels)._value.get(), 5.0)
        self.assertEqual(exporter.IPERF_JITTER_MS.labels(**self.labels)._value.get(), 0)

    def test_parse_udp_success(self):
        udp_json = {
            "end": {
                "sum": {
                    "seconds": 5.0,
                    "bytes": 1000000,
                    "bits_per_second": 1600000.0,
                    "jitter_ms": 1.25,
                    "packets": 1000,
                    "lost_packets": 2
                }
            }
        }

        exporter.parse_and_publish_metrics(udp_json, self.source, self.dest, 'udp')

        self.assertEqual(exporter.IPERF_TEST_SUCCESS.labels(**self.udp_labels)._value.get(), 1)
        self.assertAlmostEqual(exporter.IPERF_BANDWIDTH_MBPS.labels(**self.udp_labels)._value.get(), 1.6)
        self.assertEqual(exporter.IPERF_JITTER_MS.labels(**self.udp_labels)._value.get(), 1.25)
        self.assertEqual(exporter.IPERF_PACKETS_TOTAL.labels(**self.udp_labels)._value.get(), 1000)
        self.assertEqual(exporter.IPERF_LOST_PACKETS.labels(**self.udp_labels)._value.get(), 2)
        self.assertEqual(exporter.IPERF_TCP_RETRANSMITS.labels(**self.udp_labels)._value.get(), 0)

    def test_parse_error_result(self):
        err_json = {
            "error": "error - unable to connect to server: Connection timed out"
        }

        exporter.parse_and_publish_metrics(err_json, self.source, self.dest, 'tcp')

        self.assertEqual(exporter.IPERF_TEST_SUCCESS.labels(**self.labels)._value.get(), 0)
        self.assertEqual(exporter.IPERF_BANDWIDTH_MBPS.labels(**self.labels)._value.get(), 0)
        self.assertEqual(exporter.IPERF_TCP_RETRANSMITS.labels(**self.labels)._value.get(), 0)
        self.assertEqual(exporter.IPERF_TCP_RTT_MS.labels(**self.labels)._value.get(), 0)

    @patch("subprocess.Popen")
    def test_stall_exception_raised_on_stall(self, mock_popen):
        # Simulate a process that never terminates and produces no I/O progress
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None  # Process is running
        fake_proc.pid = 999999  # Non-existent PID to simulate no /proc IO advancement
        mock_popen.return_value = fake_proc

        with self.assertRaises(exporter.IperfTimeoutException):
            exporter.execute_iperf_test(
                server_ip="127.0.0.1",
                server_port=5201,
                protocol="tcp",
                duration=1,
                connect_timeout_ms=500,
                stall_threshold_sec=1,
            )

        fake_proc.kill.assert_called()

if __name__ == '__main__':
    unittest.main()
