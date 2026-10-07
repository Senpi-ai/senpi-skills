#!/usr/bin/env python3
"""Pulse Scanner — Adaptive Market Signal Scanner

Universal scanner that auto-tunes polling intervals based on market signal density.
Extracts adaptive logic from WOLF orchestrator for general-purpose use.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

# Import shared utilities
sys.path.insert(0, str(Path(__file__).parent))
from pulse_config import (
    PulseConfig, PulseError, MCPError,
    load_config, mcporter_call, atomic_write,
    load_heat_state, save_heat_state,
    validate_signal_source
)


class ScannerError(PulseError):
    """Scanner-specific error."""
    pass


def log(msg: str, verbose: bool = True) -> None:
    """Log message to stderr if verbose enabled."""
    if verbose:
        timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"[{timestamp}] {msg}", file=sys.stderr)


def call_signal_source(config: PulseConfig, verbose: bool = True) -> Dict[str, Any]:
    """Call the configured signal source and return parsed results.
    
    Args:
        config: Pulse configuration
        verbose: Whether to log debug information
        
    Returns:
        Signal source response dict
        
    Raises:
        ScannerError: If signal source call fails
    """
    signal_source = validate_signal_source(config.signal_source)
    log(f"Calling signal source: {signal_source}", verbose)
    
    try:
        # Check if it's a script path
        if signal_source.startswith("/") or signal_source.startswith("./"):
            # Call external script
            result = subprocess.run(
                ["python3", signal_source],
                capture_output=True,
                text=True,
                timeout=config.mcp_timeout
            )
            
            if result.returncode != 0:
                raise ScannerError(f"Script failed: {result.stderr}")
            
            if not result.stdout.strip():
                raise ScannerError("Empty script output")
            
            return json.loads(result.stdout)
        
        else:
            # Call MCP tool
            return mcporter_call(
                signal_source,
                {},  # Most signal sources take no params
                timeout=config.mcp_timeout,
                retries=config.mcp_retries
            )
    
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, json.JSONDecodeError) as e:
        raise ScannerError(f"Signal source error: {e}")
    except MCPError as e:
        raise ScannerError(f"MCP error: {e}")


def extract_signals(response: Dict[str, Any], config: PulseConfig, verbose: bool = True) -> List[Dict[str, Any]]:
    """Extract and filter signals from source response.
    
    Args:
        response: Raw signal source response
        config: Pulse configuration for filtering
        verbose: Whether to log debug information
        
    Returns:
        List of qualifying signals
    """
    # Handle different response formats
    signals = []
    
    # Check common response patterns
    if "data" in response and "signals" in response["data"]:
        signals = response["data"]["signals"]
    elif "signals" in response:
        signals = response["signals"]
    elif "firstJumps" in response:
        # WOLF scanner format - combine all signal types
        signals.extend(response.get("firstJumps", []))
        signals.extend(response.get("immediateMovers", []))
        signals.extend(response.get("contribExplosions", []))
        signals.extend(response.get("deepClimbers", []))
    else:
        # Try direct array access
        if isinstance(response, list):
            signals = response
        elif "data" in response and isinstance(response["data"], list):
            signals = response["data"]
    
    log(f"Extracted {len(signals)} raw signals", verbose)
    
    # Apply mechanical filters
    filtered = []
    
    for signal in signals:
        try:
            # Basic validation
            if not isinstance(signal, dict):
                continue
            
            # Trader count filter
            traders = signal.get("traders", 0)
            if traders < config.min_traders:
                continue
            
            # Velocity filter
            velocity = Decimal(str(signal.get("contribVelocity", 0)))
            if velocity < config.min_velocity:
                continue
            
            # Price change filter (if available)
            price_chg_4h = signal.get("priceChg4h")
            if price_chg_4h is not None:
                abs_change = abs(Decimal(str(price_chg_4h)))
                if abs_change > config.max_price_change_4h:
                    continue
            
            # Skip erratic/low velocity signals (WOLF-style filters)
            if signal.get("erratic", False) or signal.get("lowVelocity", False):
                continue
            
            filtered.append(signal)
            
        except (TypeError, ValueError) as e:
            log(f"Warning: Invalid signal data: {e}", verbose)
            continue
    
    log(f"Filtered to {len(filtered)} qualifying signals", verbose)
    return filtered


def determine_action(signals: List[Dict[str, Any]], config: PulseConfig, verbose: bool = True) -> str:
    """Determine if escalation is needed based on signal characteristics.
    
    Args:
        signals: List of qualifying signals
        config: Pulse configuration  
        verbose: Whether to log debug information
        
    Returns:
        "escalate" if action needed, "none" otherwise
    """
    if not signals:
        return "none"
    
    # Simple escalation criteria - can be extended
    high_velocity_signals = []
    
    for signal in signals:
        velocity = Decimal(str(signal.get("contribVelocity", 0)))
        traders = signal.get("traders", 0)
        
        # High-conviction signals: high velocity + trader count  
        # Use 10x the minimum velocity threshold as escalation criteria
        escalation_velocity_threshold = config.min_velocity * 10
        if velocity > escalation_velocity_threshold and traders > 50:
            high_velocity_signals.append(signal)
    
    if high_velocity_signals:
        log(f"Found {len(high_velocity_signals)} high-conviction signals - escalating", verbose)
        return "escalate"
    
    log("No escalation criteria met", verbose)
    return "none"


def update_heat_level(
    signals: List[Dict[str, Any]], 
    action: str,
    heat_state: Dict[str, Any], 
    config: PulseConfig,
    verbose: bool = True
) -> Dict[str, Any]:
    """Update heat level based on current signals and state.
    
    Args:
        signals: Qualified signals from this scan
        action: Determined action ("escalate" | "none")
        heat_state: Current heat state
        config: Pulse configuration
        verbose: Whether to log debug information
        
    Returns:
        Updated heat state dict
    """
    current_level = heat_state.get("level", "cold")
    consecutive_empty = heat_state.get("consecutiveEmpty", 0)
    signal_count = len(signals)
    
    log(f"Heat update: level={current_level}, signals={signal_count}, action={action}, consecutiveEmpty={consecutive_empty}", verbose)
    
    new_state = heat_state.copy()
    new_state["updatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    
    # Signal-based state transitions
    if signal_count == 0:
        # No signals - increment empty counter
        new_state["consecutiveEmpty"] = consecutive_empty + 1
        
        # Decay to COLD if threshold reached
        if consecutive_empty + 1 >= config.decay_threshold:
            new_state["level"] = "cold"
            log(f"Decaying to COLD (consecutiveEmpty={consecutive_empty + 1} >= {config.decay_threshold})", verbose)
    
    else:
        # Signals detected - reset empty counter
        new_state["consecutiveEmpty"] = 0
        
        if action == "escalate":
            # Immediate escalation to HOT
            new_state["level"] = "hot"
            new_state["lastEscalation"] = new_state["updatedAt"]
            log(f"Escalating to HOT (action={action})", verbose)
        
        elif current_level == "cold" and signal_count >= config.warm_threshold:
            # COLD -> WARM
            new_state["level"] = "warm"
            log(f"Warming up (signals={signal_count} >= {config.warm_threshold})", verbose)
        
        elif current_level == "warm" and signal_count >= config.hot_threshold:
            # WARM -> HOT
            new_state["level"] = "hot"
            new_state["lastEscalation"] = new_state["updatedAt"]
            log(f"Heating up (signals={signal_count} >= {config.hot_threshold})", verbose)
    
    # Hot persistence logic
    if current_level == "hot" and new_state["level"] == "hot" and action == "none":
        last_escalation = heat_state.get("lastEscalation")
        if last_escalation:
            try:
                escalation_time = datetime.fromisoformat(last_escalation.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                cycles_since = (now - escalation_time).total_seconds() / (config.hot_interval_ms / 1000)
                
                if cycles_since >= config.hot_persistence:
                    new_state["level"] = "warm"
                    log(f"HOT persistence expired (cycles={cycles_since:.1f} >= {config.hot_persistence})", verbose)
            except (ValueError, TypeError) as e:
                log(f"Warning: Invalid lastEscalation timestamp: {e}", verbose)
    
    return new_state


def get_next_interval_ms(level: str, config: PulseConfig) -> int:
    """Get the polling interval for the given heat level.
    
    Args:
        level: Heat level ("cold" | "warm" | "hot")
        config: Pulse configuration
        
    Returns:
        Interval in milliseconds
    """
    intervals = {
        "cold": config.cold_interval_ms,
        "warm": config.warm_interval_ms,
        "hot": config.hot_interval_ms
    }
    
    return intervals.get(level, config.cold_interval_ms)


def execute_escalation_action(signals: List[Dict[str, Any]], config: PulseConfig, verbose: bool = True) -> None:
    """Execute the configured escalation action.
    
    Args:
        signals: Qualifying signals that triggered escalation
        config: Pulse configuration
        verbose: Whether to log debug information
    """
    if config.escalation_action == "alert" and config.alert_target:
        try:
            # Send notification (example for Telegram)
            message = f"🔥 Pulse Scanner Alert: {len(signals)} signals detected"
            
            # This is a simplified example - real implementation would use message tool
            log(f"Alert: {message} (target: {config.alert_target})", verbose)
        
        except Exception as e:
            log(f"Warning: Failed to send alert: {e}", verbose)
    
    elif config.escalation_action == "script" and config.escalation_script:
        try:
            # Run custom escalation script
            subprocess.run([
                "python3", config.escalation_script,
                "--signals", json.dumps(signals)
            ], timeout=30)
            log(f"Executed escalation script: {config.escalation_script}", verbose)
        
        except Exception as e:
            log(f"Warning: Failed to run escalation script: {e}", verbose)
    
    elif config.escalation_action == "wake_main":
        # This would wake the main session (implementation depends on OpenClaw setup)
        log("Escalation action: wake_main (not implemented in this example)", verbose)


def main() -> int:
    """Main scanner logic."""
    parser = argparse.ArgumentParser(description="Pulse adaptive scanner")
    parser.add_argument("--config-path", help="Configuration file path")
    parser.add_argument("--dry-run", action="store_true", help="Dry run without state updates")
    parser.add_argument("--verbose", action="store_true", default=True, help="Verbose logging")
    parser.add_argument("--quiet", action="store_true", help="Suppress logging")
    
    args = parser.parse_args()
    
    verbose = args.verbose and not args.quiet
    
    try:
        # Load configuration
        config = load_config(args.config_path)
        log(f"Loaded config: instance_id={config.instance_id}, signal_source={config.signal_source}", verbose)
        
        # Load current heat state
        heat_state = load_heat_state(config)
        current_level = heat_state.get("level", "cold")
        log(f"Current heat level: {current_level}", verbose)
        
        # Call signal source
        response = call_signal_source(config, verbose)
        
        # Check for errors in response
        if response.get("status") == "error":
            error_msg = response.get("error", "Unknown error")
            raise ScannerError(f"Signal source error: {error_msg}")
        
        # Extract and filter signals
        signals = extract_signals(response, config, verbose)
        
        # Determine action
        action = determine_action(signals, config, verbose)
        
        # Update heat state
        new_heat_state = update_heat_level(signals, action, heat_state, config, verbose)
        new_level = new_heat_state.get("level", "cold")
        
        # Save state (unless dry run)
        if not args.dry_run:
            save_heat_state(config, new_heat_state)
            log(f"Updated heat state: {current_level} -> {new_level}", verbose)
        else:
            log(f"Dry run: would update {current_level} -> {new_level}", verbose)
        
        # Execute escalation action if needed
        if action == "escalate" and not args.dry_run:
            execute_escalation_action(signals, config, verbose)
        
        # Check for state change to decide early exit
        if current_level == new_level and action == "none" and len(signals) == 0:
            if not args.dry_run:
                print("HEARTBEAT_OK")
                return 0
        
        # Build output result
        result = {
            "status": "ok",
            "action": action,
            "heat": new_level,
            "candidates": [
                {
                    "token": s.get("token", "unknown"),
                    "direction": s.get("direction", "unknown"),
                    "traders": s.get("traders", 0),
                    "velocity": str(Decimal(str(s.get("contribVelocity", 0)))),
                    "reasons": s.get("reasons", []),
                }
                for s in signals[:10]  # Limit output size
            ],
            "interval_ms": get_next_interval_ms(new_level, config),
            "stats": {
                "signal_count": len(signals),
                "heat_level": new_level,
                "consecutive_empty": new_heat_state.get("consecutiveEmpty", 0),
                "instance_id": config.instance_id,
                "timestamp": new_heat_state.get("updatedAt")
            }
        }
        
        print(json.dumps(result))
        return 0
    
    except KeyboardInterrupt:
        log("Interrupted by user", verbose)
        return 1
    
    except Exception as e:
        log(f"Scanner error: {e}", verbose)
        error_result = {
            "status": "error",
            "error": str(e)
        }
        print(json.dumps(error_result))
        return 1


if __name__ == "__main__":
    sys.exit(main())