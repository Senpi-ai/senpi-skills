#!/usr/bin/env python3
"""Pulse Configuration and Shared Utilities

Provides centralized config loading, MCP calls, and atomic file operations.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, asdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


class PulseError(Exception):
    """Base exception for Pulse scanner errors."""
    pass


class ConfigError(PulseError):
    """Configuration loading/validation error."""
    pass


class MCPError(PulseError):
    """MCP call error."""
    pass


@dataclass(frozen=True)
class PulseConfig:
    """Pulse scanner configuration with all tunable parameters."""
    
    # Core settings
    signal_source: str = "leaderboard_get_smart_money_token_inflows"
    state_file: str = "pulse-heat.json"
    instance_id: str = "default"
    
    # Heat thresholds
    warm_threshold: int = 2
    hot_threshold: int = 5
    decay_threshold: int = 3
    hot_persistence: int = 5
    
    # Polling intervals (milliseconds)
    cold_interval_ms: int = 300000  # 5 minutes
    warm_interval_ms: int = 180000  # 3 minutes
    hot_interval_ms: int = 90000    # 90 seconds
    
    # Signal filtering
    min_traders: int = 10
    min_velocity: Decimal = Decimal("1.0")
    max_price_change_4h: Decimal = Decimal("50.0")  # 50%
    
    # Entry actions
    escalation_action: str = "alert"  # "alert" | "script" | "wake_main"
    escalation_script: Optional[str] = None
    alert_target: Optional[str] = None
    
    # Directories
    state_dir: str = "/data/workspace/state/pulse"
    
    # MCP settings
    mcp_timeout: int = 30
    mcp_retries: int = 2


def load_config(config_path: Optional[str] = None) -> PulseConfig:
    """Load configuration from file with deep merge over defaults.
    
    Args:
        config_path: Path to JSON config file. If None, uses defaults.
        
    Returns:
        PulseConfig instance with merged configuration.
        
    Raises:
        ConfigError: If config file is invalid or unreadable.
    """
    if config_path is None or not Path(config_path).exists():
        return PulseConfig()
    
    try:
        with open(config_path, 'r') as f:
            user_config = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ConfigError(f"Failed to load config from {config_path}: {e}")
    
    if not isinstance(user_config, dict):
        raise ConfigError(f"Config must be a JSON object, got {type(user_config)}")
    
    # Deep merge with defaults
    defaults = asdict(PulseConfig())
    merged = {**defaults, **user_config}
    
    # Convert string decimals
    decimal_fields = {"min_velocity", "max_price_change_4h"}
    for field in decimal_fields:
        if field in merged and not isinstance(merged[field], Decimal):
            try:
                merged[field] = Decimal(str(merged[field]))
            except Exception as e:
                raise ConfigError(f"Invalid decimal value for {field}: {e}")
    
    try:
        return PulseConfig(**merged)
    except TypeError as e:
        raise ConfigError(f"Invalid config parameters: {e}")


def mcporter_call(tool: str, params: Optional[Dict[str, Any]] = None, timeout: int = 30, retries: int = 2) -> Dict[str, Any]:
    """Centralized MCP call wrapper with retry and error handling.
    
    Args:
        tool: MCP tool name (e.g., "leaderboard_get_smart_money_token_inflows")
        params: Tool parameters as dict
        timeout: Subprocess timeout in seconds
        retries: Number of retry attempts
        
    Returns:
        Parsed JSON response from MCP tool
        
    Raises:
        MCPError: If all retry attempts fail or response is invalid
    """
    if params is None:
        params = {}
    
    args_json = json.dumps(params)
    cmd = ["mcporter", "call", "senpi", tool, "--args", args_json]
    
    last_error = None
    
    for attempt in range(retries + 1):
        try:
            if attempt > 0:
                # Brief backoff on retry
                time.sleep(min(2 ** attempt, 5))
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            
            if result.returncode != 0:
                error_msg = result.stderr.strip() or "Unknown error"
                last_error = MCPError(f"MCP call failed (rc={result.returncode}): {error_msg}")
                continue
            
            if not result.stdout.strip():
                last_error = MCPError("Empty response from MCP tool")
                continue
            
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError as e:
                last_error = MCPError(f"Invalid JSON response: {e}")
                continue
                
        except subprocess.TimeoutExpired:
            last_error = MCPError(f"MCP call timeout after {timeout}s")
            continue
        except Exception as e:
            last_error = MCPError(f"MCP call error: {e}")
            continue
    
    # All retries exhausted
    raise last_error or MCPError("Unknown MCP call failure")


def atomic_write(path: Union[str, Path], data: Union[str, bytes, Dict[str, Any]]) -> None:
    """Write data to file atomically using temp file + rename.
    
    Args:
        path: Target file path
        data: Data to write (str, bytes, or dict for JSON)
        
    Raises:
        PulseError: If write operation fails
    """
    path = Path(path)
    
    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)
    
    # Prepare data
    if isinstance(data, dict):
        content = json.dumps(data, indent=2, ensure_ascii=False)
        mode = 'w'
    elif isinstance(data, str):
        content = data
        mode = 'w'
    elif isinstance(data, bytes):
        content = data
        mode = 'wb'
    else:
        raise PulseError(f"Unsupported data type for atomic write: {type(data)}")
    
    # Write to temp file in same directory
    try:
        with tempfile.NamedTemporaryFile(
            mode=mode,
            dir=path.parent,
            prefix=f".{path.name}.tmp",
            delete=False
        ) as tmp_file:
            tmp_file.write(content)
            tmp_path = tmp_file.name
        
        # Atomic rename
        os.rename(tmp_path, path)
        
    except Exception as e:
        # Cleanup temp file if it exists
        try:
            os.unlink(tmp_path)
        except (OSError, FileNotFoundError):
            # Temp file cleanup failure is not critical
            pass
        raise PulseError(f"Atomic write failed for {path}: {e}")


def get_state_file_path(config: PulseConfig) -> Path:
    """Get the full path to the state file for this instance.
    
    Args:
        config: Pulse configuration
        
    Returns:
        Path to instance-specific state file
    """
    state_dir = Path(config.state_dir)
    if config.instance_id == "default":
        return state_dir / config.state_file
    else:
        # Instance-specific state file
        base_name = Path(config.state_file).stem
        extension = Path(config.state_file).suffix
        instance_name = f"{base_name}-{config.instance_id}{extension}"
        return state_dir / instance_name


def load_heat_state(config: PulseConfig) -> Dict[str, Any]:
    """Load heat state from file, creating default if missing.
    
    Args:
        config: Pulse configuration
        
    Returns:
        Heat state dict
        
    Raises:
        PulseError: If state file is corrupt or has invalid format
    """
    state_file = get_state_file_path(config)
    
    if not state_file.exists():
        # Create default state
        default_state = {
            "level": "cold",
            "consecutiveEmpty": 0,
            "lastEscalation": None,
            "updatedAt": None
        }
        atomic_write(state_file, default_state)
        return default_state
    
    try:
        with open(state_file, 'r') as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise PulseError(f"Failed to load heat state from {state_file}: {e}")
    
    # Data contract validation
    if not isinstance(state, dict):
        raise PulseError(f"Heat state must be a dict, got {type(state)}")
    
    # Validate required fields and types
    required_fields = {
        "level": str,
        "consecutiveEmpty": int,
        "lastEscalation": (str, type(None)),
        "updatedAt": (str, type(None))
    }
    
    for field, expected_type in required_fields.items():
        if field not in state:
            raise PulseError(f"Missing required field in heat state: {field}")
        
        value = state[field]
        if not isinstance(value, expected_type):
            raise PulseError(f"Invalid type for heat state field '{field}': expected {expected_type}, got {type(value)}")
    
    # Validate level enum
    valid_levels = {"cold", "warm", "hot"}
    if state["level"] not in valid_levels:
        raise PulseError(f"Invalid heat level '{state['level']}', must be one of {valid_levels}")
    
    # Validate consecutiveEmpty is non-negative
    if state["consecutiveEmpty"] < 0:
        raise PulseError(f"consecutiveEmpty must be non-negative, got {state['consecutiveEmpty']}")
    
    return state


def save_heat_state(config: PulseConfig, state: Dict[str, Any]) -> None:
    """Save heat state to file atomically.
    
    Args:
        config: Pulse configuration
        state: Heat state dict to save
    """
    state_file = get_state_file_path(config)
    atomic_write(state_file, state)


def validate_signal_source(signal_source: str) -> str:
    """Validate and normalize signal source configuration.
    
    Args:
        signal_source: Signal source identifier
        
    Returns:
        Validated signal source
        
    Raises:
        ConfigError: If signal source is invalid
    """
    # Known MCP tools
    valid_tools = {
        "leaderboard_get_smart_money_token_inflows",
        "leaderboard_get_trader_performance",
        "leaderboard_get_top_traders",
        "market_get_trending_tokens"
    }
    
    if signal_source in valid_tools:
        return signal_source
    
    # Check if it's a file path
    if signal_source.startswith("/") or signal_source.startswith("./"):
        if not Path(signal_source).exists():
            raise ConfigError(f"Signal source script not found: {signal_source}")
        return signal_source
    
    # Assume it's an MCP tool name (extensible)
    return signal_source


if __name__ == "__main__":
    # Configuration module - no direct execution
    print("This is a configuration module. Import it from other scripts.")
    import sys
    sys.exit(1)