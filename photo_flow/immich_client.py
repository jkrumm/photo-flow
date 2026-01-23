"""
Immich API client for triggering external library scans.

This module provides a simple client to interact with the Immich API
after backup operations complete.
"""
import json
import logging
import urllib.request
import urllib.error
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ImmichLibrary:
    """Represents an Immich library."""
    id: str
    name: str
    owner_id: str
    import_paths: list
    exclusion_patterns: list
    asset_count: int


class ImmichClient:
    """
    Simple HTTP client for Immich API operations.

    Uses only stdlib (urllib) to avoid extra dependencies.
    """

    def __init__(self, base_url: str, api_key: str):
        """
        Initialize the Immich client.

        Args:
            base_url: Immich server URL (e.g., https://immich.jkrumm.com)
            api_key: API key with library.read and library.update permissions
        """
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key

    def _request(self, method: str, endpoint: str, data: Optional[dict] = None) -> dict:
        """
        Make an HTTP request to the Immich API.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (e.g., /api/libraries)
            data: Optional JSON body data

        Returns:
            Parsed JSON response or empty dict for 204 responses

        Raises:
            urllib.error.HTTPError: On HTTP errors
            urllib.error.URLError: On connection errors
        """
        url = f"{self.base_url}{endpoint}"
        headers = {
            "x-api-key": self.api_key,
            "Accept": "application/json",
            "User-Agent": "PhotoFlow/1.0",
        }

        body = None
        if data:
            headers["Content-Type"] = "application/json"
            body = json.dumps(data).encode('utf-8')

        req = urllib.request.Request(url, data=body, headers=headers, method=method)

        with urllib.request.urlopen(req, timeout=30) as response:
            if response.status == 204:
                return {}
            return json.loads(response.read().decode('utf-8'))

    def get_libraries(self) -> list[ImmichLibrary]:
        """
        Get all libraries from Immich.

        Returns:
            List of ImmichLibrary objects
        """
        response = self._request("GET", "/api/libraries")

        libraries = []
        for lib in response:
            libraries.append(ImmichLibrary(
                id=lib.get("id", ""),
                name=lib.get("name", ""),
                owner_id=lib.get("ownerId", ""),
                import_paths=lib.get("importPaths", []),
                exclusion_patterns=lib.get("exclusionPatterns", []),
                asset_count=lib.get("assetCount", 0)
            ))

        return libraries

    def scan_library(self, library_id: str) -> tuple[bool, str]:
        """
        Trigger a scan on the specified library.

        Args:
            library_id: UUID of the library to scan

        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            self._request("POST", f"/api/libraries/{library_id}/scan")
            return True, f"Scan triggered for library {library_id}"
        except urllib.error.HTTPError as e:
            error_msg = f"HTTP {e.code}: {e.reason}"
            try:
                error_body = json.loads(e.read().decode('utf-8'))
                if "message" in error_body:
                    error_msg = error_body["message"]
            except Exception:
                pass
            return False, error_msg
        except urllib.error.URLError as e:
            return False, f"Connection error: {e.reason}"
        except Exception as e:
            return False, str(e)

    def scan_all_libraries(self) -> tuple[bool, str]:
        """
        Trigger a scan on all external libraries.

        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            libraries = self.get_libraries()

            if not libraries:
                return False, "No libraries found"

            results = []
            all_success = True

            for lib in libraries:
                success, msg = self.scan_library(lib.id)
                results.append(f"{lib.name}: {msg}")
                if not success:
                    all_success = False

            return all_success, "; ".join(results)

        except Exception as e:
            return False, str(e)


def trigger_immich_scan(base_url: Optional[str] = None, api_key: Optional[str] = None) -> tuple[bool, str]:
    """
    Convenience function to trigger Immich library scan.

    Uses environment variables if parameters not provided.

    Args:
        base_url: Immich server URL (defaults to IMMICH_URL env var)
        api_key: API key (defaults to IMMICH_API_KEY env var)

    Returns:
        Tuple of (success: bool, message: str)
    """
    import os
    from dotenv import load_dotenv

    # Load .env file from project root
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(project_root, '.env'))

    url = base_url or os.getenv("IMMICH_URL")
    key = api_key or os.getenv("IMMICH_API_KEY")

    if not url or not key:
        return False, "Immich URL or API key not configured"

    client = ImmichClient(url, key)
    return client.scan_all_libraries()
