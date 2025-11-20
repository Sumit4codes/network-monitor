#!/usr/bin/env python3
import psutil
import time
import os
import sys
import curses
from datetime import datetime
from collections import defaultdict

# --- Data Fetching ---

def get_process_name(pid):
    try:
        process = psutil.Process(pid)
        return process.name(), process.username()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return "N/A", "N/A"

def get_io_counters(pid):
    try:
        process = psutil.Process(pid)
        io = process.io_counters()
        return io.read_bytes, io.write_bytes
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return 0, 0

def format_bytes(size):
    power = 2**10
    n = 0
    power_labels = {0 : '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.1f}{power_labels[n]}B/s"

def get_connections(prev_io_stats):
    connections = []
    current_io_stats = {}
    
    pids_with_conn = set()
    try:
        for conn in psutil.net_connections(kind='inet'):
            if conn.pid:
                pids_with_conn.add(conn.pid)
                
                name, user = get_process_name(conn.pid)
                laddr = f"{conn.laddr.ip}:{conn.laddr.port}"
                raddr = f"{conn.raddr.ip}:{conn.raddr.port}" if conn.raddr else "N/A"
                
                connections.append({
                    "pid": conn.pid,
                    "name": name,
                    "user": user,
                    "laddr": laddr,
                    "raddr": raddr,
                    "status": conn.status,
                    "read_rate": 0,
                    "write_rate": 0,
                    "total_rate": 0
                })
    except psutil.AccessDenied:
        pass

    for pid in pids_with_conn:
        r_bytes, w_bytes = get_io_counters(pid)
        current_io_stats[pid] = (r_bytes, w_bytes)
        
        if pid in prev_io_stats:
            prev_r, prev_w = prev_io_stats[pid]
            r_rate = max(0, r_bytes - prev_r)
            w_rate = max(0, w_bytes - prev_w)
            
            for conn in connections:
                if conn['pid'] == pid:
                    conn['read_rate'] = r_rate
                    conn['write_rate'] = w_rate
                    conn['total_rate'] = r_rate + w_rate

    return connections, current_io_stats

# --- UI & Logic ---

SORT_MODES = [
    ("Rate", lambda x: (x['total_rate'], x['pid'])),
    ("PID", lambda x: x['pid']),
    ("Name", lambda x: x['name'])
]

def kill_process(stdscr, pid, name):
    stdscr.nodelay(False)
    curses.echo()
    try:
        stdscr.addstr(0, 0, f"Kill {name} (PID {pid})? (y/n): " + " " * 20, curses.A_REVERSE | curses.color_pair(3))
        resp = stdscr.getkey()
        if resp.lower() == 'y':
            try:
                p = psutil.Process(pid)
                p.terminate()
                stdscr.addstr(0, 0, f"Terminated PID {pid}. Press any key...", curses.A_REVERSE)
            except psutil.NoSuchProcess:
                stdscr.addstr(0, 0, f"PID {pid} not found. Press any key...", curses.A_REVERSE)
            except psutil.AccessDenied:
                stdscr.addstr(0, 0, f"Access denied. Try sudo. Press any key...", curses.A_REVERSE)
            except Exception as e:
                stdscr.addstr(0, 0, f"Error: {e}. Press any key...", curses.A_REVERSE)
            stdscr.getch()
    except Exception:
        pass
    finally:
        curses.noecho()
        stdscr.nodelay(True)

def draw_menu(stdscr, connections, selected_idx, scroll_offset, sort_mode_idx):
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    
    # --- Top Bar ---
    sort_name = SORT_MODES[sort_mode_idx][0]
    title = f" Network Monitor | Sort: {sort_name} (s) | Kill (k) | Quit (q) "
    # Ensure title fits
    title = title[:width-1]
    stdscr.attron(curses.color_pair(1) | curses.A_BOLD)
    stdscr.addstr(0, 0, title)
    stdscr.clrtoeol()
    stdscr.attroff(curses.color_pair(1) | curses.A_BOLD)
    
    # --- Header ---
    header = f"{'PID':<8} {'USER':<12} {'PROCESS':<18} {'DL RATE':<10} {'UL RATE':<10} {'LADDR':<22} {'RADDR':<22}"
    stdscr.attron(curses.color_pair(2) | curses.A_BOLD)
    stdscr.addstr(1, 0, header[:width-1])
    stdscr.clrtoeol()
    stdscr.attroff(curses.color_pair(2) | curses.A_BOLD)
    
    # --- List ---
    # Deduplicate for display logic (group by PID visually or just list connections?)
    # The user wants to see connections, but selecting a row usually implies selecting the process.
    # We will list all connections. If multiple connections belong to same PID, selecting any of them selects that PID.
    
    # Apply Sort
    key_func = SORT_MODES[sort_mode_idx][1]
    reverse = True if sort_name == "Rate" else False
    connections.sort(key=key_func, reverse=reverse)
    
    max_rows = height - 3 # Title + Header + Status/Bottom
    
    visible_conns = connections[scroll_offset : scroll_offset + max_rows]
    
    seen_pids = set()
    # We need to track seen PIDs globally for the whole list to hide duplicates correctly?
    # Actually, if we scroll, we might hide the "first" occurrence. 
    # Simplification: Just show data for every row, or only hide if previous row in *visible* list was same PID.
    # Let's show data for every row to avoid confusion when scrolling.
    
    for i, conn in enumerate(visible_conns):
        row_idx = 2 + i
        if row_idx >= height - 1:
            break
            
        is_selected = (i + scroll_offset == selected_idx)
        
        dl_str = format_bytes(conn['read_rate'])
        ul_str = format_bytes(conn['write_rate'])
        proc_name = (conn['name'][:15] + '..') if len(conn['name']) > 17 else conn['name']
        
        line = f"{conn['pid']:<8} {conn['user']:<12} {proc_name:<18} {dl_str:<10} {ul_str:<10} {conn['laddr']:<22} {conn['raddr']:<22}"
        
        style = curses.A_NORMAL
        if is_selected:
            style = curses.A_REVERSE | curses.color_pair(3)
        
        try:
            stdscr.addstr(row_idx, 0, line[:width-1], style)
            stdscr.clrtoeol()
        except curses.error:
            pass

    # --- Status Bar ---
    status = f" Total Connections: {len(connections)} | Selected: {selected_idx + 1}/{len(connections)} "
    try:
        stdscr.attron(curses.color_pair(1))
        stdscr.addstr(height - 1, 0, status[:width-1])
        stdscr.clrtoeol()
        stdscr.attroff(curses.color_pair(1))
    except curses.error:
        pass

    stdscr.refresh()

def main(stdscr):
    # Setup colors
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLUE)  # Header/Footer
    curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_CYAN)  # Column Headers
    curses.init_pair(3, curses.COLOR_BLACK, curses.COLOR_WHITE) # Selection
    
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(1000)
    
    prev_io_stats = {}
    connections = []
    
    selected_idx = 0
    scroll_offset = 0
    sort_mode_idx = 0
    
    while True:
        # Fetch Data
        new_conns, curr_io_stats = get_connections(prev_io_stats)
        prev_io_stats = curr_io_stats
        
        # Preserve selection if possible
        # If list size changes, clamp selection
        if new_conns:
            connections = new_conns
        
        if selected_idx >= len(connections):
            selected_idx = max(0, len(connections) - 1)
            
        # Draw
        draw_menu(stdscr, connections, selected_idx, scroll_offset, sort_mode_idx)
        
        # Input
        try:
            key = stdscr.getch()
            
            if key == curses.ERR:
                continue
                
            if key == ord('q'):
                break
            elif key == curses.KEY_UP:
                selected_idx = max(0, selected_idx - 1)
                if selected_idx < scroll_offset:
                    scroll_offset = selected_idx
            elif key == curses.KEY_DOWN:
                selected_idx = min(len(connections) - 1, selected_idx + 1)
                max_rows = stdscr.getmaxyx()[0] - 3
                if selected_idx >= scroll_offset + max_rows:
                    scroll_offset = selected_idx - max_rows + 1
            elif key == ord('s'):
                sort_mode_idx = (sort_mode_idx + 1) % len(SORT_MODES)
            elif key == ord('k'):
                if connections:
                    target = connections[selected_idx]
                    kill_process(stdscr, target['pid'], target['name'])
                    
        except Exception:
            pass

if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        sys.exit(0)
