#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sub2API Windows GUI Launcher
Tkinter GUI for managing Sub2API configuration and service on Windows.
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import subprocess
import threading
import os
import sys
import secrets
import queue
import time
from pathlib import Path
from datetime import datetime

try:
    import yaml
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyyaml"])
    import yaml


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
APP_TITLE = "Sub2API 管理面板"
DEFAULT_CONFIG_NAME = "config.yaml"
DEFAULT_EXE_NAME = "sub2api.exe"
WINDOW_WIDTH = 960
WINDOW_HEIGHT = 720


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------
def generate_hex_secret(length=32):
    """Generate a random hex string (length bytes -> 2*length hex chars)."""
    return secrets.token_hex(length)


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base* (mutates base)."""
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def get_default_config() -> dict:
    """Return a sensible default config dict."""
    return {
        "server": {
            "host": "0.0.0.0",
            "port": 8080,
            "mode": "release",
            "frontend_url": "",
        },
        "run_mode": "standard",
        "timezone": "UTC",
        "database": {
            "host": "localhost",
            "port": 5432,
            "user": "postgres",
            "password": "",
            "dbname": "sub2api",
            "sslmode": "prefer",
            "max_open_conns": 256,
            "max_idle_conns": 128,
            "conn_max_lifetime_minutes": 30,
            "conn_max_idle_time_minutes": 5,
        },
        "redis": {
            "host": "localhost",
            "port": 6379,
            "password": "",
            "db": 0,
            "pool_size": 1024,
            "min_idle_conns": 128,
            "enable_tls": False,
        },
        "jwt": {
            "secret": "",
            "expire_hour": 24,
        },
        "totp": {
            "encryption_key": "",
        },
        "default": {
            "admin_email": "admin@example.com",
            "admin_password": "admin123",
            "user_concurrency": 5,
            "user_balance": 0,
            "api_key_prefix": "sk-",
            "rate_multiplier": 1.0,
        },
        "log": {
            "level": "info",
            "format": "console",
            "service_name": "sub2api",
            "env": "production",
            "caller": True,
            "stacktrace_level": "error",
            "output": {
                "to_stdout": True,
                "to_file": True,
                "file_path": "",
            },
            "rotation": {
                "max_size_mb": 100,
                "max_backups": 10,
                "max_age_days": 7,
                "compress": True,
                "local_time": True,
            },
        },
        "gateway": {
            "response_header_timeout": 600,
            "max_body_size": 268435456,
            "stream_data_interval_timeout": 180,
            "stream_keepalive_interval": 10,
            "tls_fingerprint": {"enabled": True},
        },
        "security": {
            "url_allowlist": {
                "enabled": False,
                "allow_private_hosts": True,
                "allow_insecure_http": True,
            },
        },
        "rate_limit": {
            "overload_cooldown_minutes": 10,
        },
    }


# ---------------------------------------------------------------------------
# Main Application
# ---------------------------------------------------------------------------
class Sub2APIGui(tk.Tk):
    """Main Tkinter application window."""

    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.minsize(800, 600)

        # State
        self.config_data: dict = get_default_config()
        self.config_path: str = ""
        self.exe_path: str = ""
        self.process: subprocess.Popen | None = None
        self.log_queue: queue.Queue = queue.Queue()
        self._closing = False

        # Detect paths
        self._detect_paths()

        # Build UI
        self._build_menu()
        self._build_toolbar()
        self._build_notebook()
        self._build_statusbar()

        # Load config if exists
        if self.config_path and os.path.isfile(self.config_path):
            self._load_config(self.config_path)

        # Periodic log poll
        self._poll_log()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Path detection
    # ------------------------------------------------------------------
    def _detect_paths(self):
        """Auto-detect config.yaml and sub2api.exe locations."""
        base = Path(os.path.dirname(os.path.abspath(__file__)))
        # config.yaml
        for candidate in [base / DEFAULT_CONFIG_NAME, base / "config" / DEFAULT_CONFIG_NAME]:
            if candidate.is_file():
                self.config_path = str(candidate)
                break
        if not self.config_path:
            self.config_path = str(base / DEFAULT_CONFIG_NAME)
        # exe
        for candidate in [base / DEFAULT_EXE_NAME, base / "backend" / DEFAULT_EXE_NAME]:
            if candidate.is_file():
                self.exe_path = str(candidate)
                break
        if not self.exe_path:
            self.exe_path = str(base / DEFAULT_EXE_NAME)

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------
    def _build_menu(self):
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="打开配置文件...", command=self._open_config_dialog)
        file_menu.add_command(label="保存配置", command=self._save_config, accelerator="Ctrl+S")
        file_menu.add_command(label="另存为...", command=self._save_config_as)
        file_menu.add_separator()
        file_menu.add_command(label="选择程序路径...", command=self._select_exe)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self._on_close)
        menubar.add_cascade(label="文件", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="关于", command=lambda: messagebox.showinfo("关于", f"{APP_TITLE}\n\nSub2API Windows GUI Launcher"))
        menubar.add_cascade(label="帮助", menu=help_menu)

        self.config(menu=menubar)
        self.bind_all("<Control-s>", lambda e: self._save_config())

    # ------------------------------------------------------------------
    # Toolbar (service control)
    # ------------------------------------------------------------------
    def _build_toolbar(self):
        toolbar = ttk.Frame(self, padding=5)
        toolbar.pack(fill=tk.X, side=tk.TOP)

        self.btn_start = ttk.Button(toolbar, text="▶ 启动服务", command=self._start_service)
        self.btn_start.pack(side=tk.LEFT, padx=2)

        self.btn_stop = ttk.Button(toolbar, text="■ 停止服务", command=self._stop_service, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=2)

        self.btn_restart = ttk.Button(toolbar, text="↻ 重启服务", command=self._restart_service, state=tk.DISABLED)
        self.btn_restart.pack(side=tk.LEFT, padx=2)

        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        self.btn_save = ttk.Button(toolbar, text="💾 保存配置", command=self._save_config)
        self.btn_save.pack(side=tk.LEFT, padx=2)

        self.lbl_status = ttk.Label(toolbar, text="● 已停止", foreground="gray")
        self.lbl_status.pack(side=tk.RIGHT, padx=8)

        ttk.Label(toolbar, text="程序路径:").pack(side=tk.LEFT, padx=(16, 2))
        self.var_exe = tk.StringVar(value=self.exe_path)
        exe_entry = ttk.Entry(toolbar, textvariable=self.var_exe, width=40)
        exe_entry.pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="...", width=3, command=self._select_exe).pack(side=tk.LEFT)

    # ------------------------------------------------------------------
    # Notebook (tabs)
    # ------------------------------------------------------------------
    def _build_notebook(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Keep references to all tk.Vars
        self.vars: dict[str, tk.Variable] = {}

        self._build_tab_server()
        self._build_tab_database()
        self._build_tab_redis()
        self._build_tab_auth()
        self._build_tab_default()
        self._build_tab_log()
        self._build_tab_advanced()
        self._build_tab_logs()

    # -- Helper to add labeled row --
    @staticmethod
    def _add_row(parent, row: int, label: str, var: tk.Variable, width=40,
                 show="", tooltip="", widget_type="entry", values=None):
        ttk.Label(parent, text=label, anchor=tk.W).grid(row=row, column=0, sticky=tk.W, padx=5, pady=3)
        if widget_type == "combo":
            w = ttk.Combobox(parent, textvariable=var, values=values or [], width=width - 2, state="readonly")
        elif widget_type == "check":
            w = ttk.Checkbutton(parent, variable=var)
        else:
            w = ttk.Entry(parent, textvariable=var, width=width, show=show)
        w.grid(row=row, column=1, sticky=tk.W, padx=5, pady=3)
        if tooltip:
            ttk.Label(parent, text=tooltip, foreground="gray", font=("", 8)).grid(row=row, column=2, sticky=tk.W, padx=5)
        return w

    def _make_var(self, key: str, default="", cls=tk.StringVar):
        v = cls(value=default)
        self.vars[key] = v
        return v

    # ---------- Tab: Server ----------
    def _build_tab_server(self):
        frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(frame, text=" 服务器 ")
        frame.columnconfigure(1, weight=1)

        r = 0
        self._add_row(frame, r, "监听地址:", self._make_var("server.host", "0.0.0.0"), tooltip="0.0.0.0 = 所有接口"); r += 1
        self._add_row(frame, r, "监听端口:", self._make_var("server.port", "8080"), tooltip="默认 8080"); r += 1
        self._add_row(frame, r, "运行模式:", self._make_var("server.mode", "release"),
                      widget_type="combo", values=["release", "debug"], tooltip="release=生产, debug=开发"); r += 1
        self._add_row(frame, r, "前端 URL:", self._make_var("server.frontend_url", ""),
                      tooltip="邮件中的外部链接地址, 如 https://example.com"); r += 1
        self._add_row(frame, r, "系统模式:", self._make_var("run_mode", "standard"),
                      widget_type="combo", values=["standard", "simple"], tooltip="simple=跳过计费"); r += 1
        self._add_row(frame, r, "时区:", self._make_var("timezone", "UTC"),
                      tooltip="Windows建议UTC, Linux可用Asia/Shanghai"); r += 1

    # ---------- Tab: Database ----------
    def _build_tab_database(self):
        frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(frame, text=" 数据库 ")
        frame.columnconfigure(1, weight=1)

        r = 0
        self._add_row(frame, r, "主机地址:", self._make_var("database.host", "localhost")); r += 1
        self._add_row(frame, r, "端口:", self._make_var("database.port", "5432")); r += 1
        self._add_row(frame, r, "用户名:", self._make_var("database.user", "postgres")); r += 1
        self._add_row(frame, r, "密码:", self._make_var("database.password", ""), show="*"); r += 1
        self._add_row(frame, r, "数据库名:", self._make_var("database.dbname", "sub2api")); r += 1
        self._add_row(frame, r, "SSL 模式:", self._make_var("database.sslmode", "prefer"),
                      widget_type="combo", values=["disable", "prefer", "require", "verify-ca", "verify-full"]); r += 1
        self._add_row(frame, r, "最大连接数:", self._make_var("database.max_open_conns", "256")); r += 1
        self._add_row(frame, r, "最大空闲连接:", self._make_var("database.max_idle_conns", "128")); r += 1
        self._add_row(frame, r, "连接最大存活(分钟):", self._make_var("database.conn_max_lifetime_minutes", "30")); r += 1
        self._add_row(frame, r, "空闲最大时间(分钟):", self._make_var("database.conn_max_idle_time_minutes", "5")); r += 1

    # ---------- Tab: Redis ----------
    def _build_tab_redis(self):
        frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(frame, text=" Redis ")
        frame.columnconfigure(1, weight=1)

        r = 0
        self._add_row(frame, r, "主机地址:", self._make_var("redis.host", "localhost")); r += 1
        self._add_row(frame, r, "端口:", self._make_var("redis.port", "6379")); r += 1
        self._add_row(frame, r, "密码:", self._make_var("redis.password", ""), show="*"); r += 1
        self._add_row(frame, r, "数据库编号:", self._make_var("redis.db", "0"), tooltip="0-15"); r += 1
        self._add_row(frame, r, "连接池大小:", self._make_var("redis.pool_size", "1024")); r += 1
        self._add_row(frame, r, "最小空闲连接:", self._make_var("redis.min_idle_conns", "128")); r += 1
        self._add_row(frame, r, "启用 TLS:", self._make_var("redis.enable_tls", False, cls=tk.BooleanVar), widget_type="check"); r += 1

    # ---------- Tab: Auth (JWT / TOTP) ----------
    def _build_tab_auth(self):
        frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(frame, text=" 安全认证 ")
        frame.columnconfigure(1, weight=1)

        r = 0
        # JWT
        ttk.Label(frame, text="── JWT 配置 ──", font=("", 10, "bold")).grid(row=r, column=0, columnspan=3, sticky=tk.W, pady=(0, 5)); r += 1
        self._add_row(frame, r, "JWT Secret:", self._make_var("jwt.secret", ""), tooltip="至少32字符随机串"); r += 1
        btn_frame_jwt = ttk.Frame(frame)
        btn_frame_jwt.grid(row=r, column=1, sticky=tk.W, padx=5)
        ttk.Button(btn_frame_jwt, text="随机生成 JWT Secret", command=lambda: self.vars["jwt.secret"].set(generate_hex_secret(32))).pack(side=tk.LEFT)
        r += 1
        self._add_row(frame, r, "Token 过期(小时):", self._make_var("jwt.expire_hour", "24"), tooltip="最大168"); r += 1

        # TOTP
        ttk.Label(frame, text="── TOTP 双因素认证 ──", font=("", 10, "bold")).grid(row=r, column=0, columnspan=3, sticky=tk.W, pady=(15, 5)); r += 1
        self._add_row(frame, r, "加密密钥:", self._make_var("totp.encryption_key", ""), tooltip="留空则每次启动自动生成"); r += 1
        btn_frame_totp = ttk.Frame(frame)
        btn_frame_totp.grid(row=r, column=1, sticky=tk.W, padx=5)
        ttk.Button(btn_frame_totp, text="随机生成 TOTP Key", command=lambda: self.vars["totp.encryption_key"].set(generate_hex_secret(32))).pack(side=tk.LEFT)
        r += 1

    # ---------- Tab: Default ----------
    def _build_tab_default(self):
        frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(frame, text=" 默认设置 ")
        frame.columnconfigure(1, weight=1)

        r = 0
        ttk.Label(frame, text="── 管理员账户 ──", font=("", 10, "bold")).grid(row=r, column=0, columnspan=3, sticky=tk.W, pady=(0, 5)); r += 1
        self._add_row(frame, r, "管理员邮箱:", self._make_var("default.admin_email", "admin@example.com")); r += 1
        self._add_row(frame, r, "管理员密码:", self._make_var("default.admin_password", "admin123"), show="*"); r += 1

        ttk.Label(frame, text="── 新用户默认 ──", font=("", 10, "bold")).grid(row=r, column=0, columnspan=3, sticky=tk.W, pady=(15, 5)); r += 1
        self._add_row(frame, r, "用户并发数:", self._make_var("default.user_concurrency", "5")); r += 1
        self._add_row(frame, r, "初始余额:", self._make_var("default.user_balance", "0")); r += 1
        self._add_row(frame, r, "API Key 前缀:", self._make_var("default.api_key_prefix", "sk-")); r += 1
        self._add_row(frame, r, "费率倍数:", self._make_var("default.rate_multiplier", "1.0")); r += 1

    # ---------- Tab: Log ----------
    def _build_tab_log(self):
        frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(frame, text=" 日志 ")
        frame.columnconfigure(1, weight=1)

        r = 0
        self._add_row(frame, r, "日志级别:", self._make_var("log.level", "info"),
                      widget_type="combo", values=["debug", "info", "warn", "error"]); r += 1
        self._add_row(frame, r, "日志格式:", self._make_var("log.format", "console"),
                      widget_type="combo", values=["console", "json"]); r += 1
        self._add_row(frame, r, "服务名称:", self._make_var("log.service_name", "sub2api")); r += 1
        self._add_row(frame, r, "环境标识:", self._make_var("log.env", "production")); r += 1
        self._add_row(frame, r, "输出到控制台:", self._make_var("log.output.to_stdout", True, cls=tk.BooleanVar), widget_type="check"); r += 1
        self._add_row(frame, r, "输出到文件:", self._make_var("log.output.to_file", True, cls=tk.BooleanVar), widget_type="check"); r += 1
        self._add_row(frame, r, "日志文件路径:", self._make_var("log.output.file_path", ""), tooltip="留空自动推导"); r += 1
        self._add_row(frame, r, "单文件上限(MB):", self._make_var("log.rotation.max_size_mb", "100")); r += 1
        self._add_row(frame, r, "保留文件数:", self._make_var("log.rotation.max_backups", "10")); r += 1
        self._add_row(frame, r, "保留天数:", self._make_var("log.rotation.max_age_days", "7")); r += 1
        self._add_row(frame, r, "压缩历史日志:", self._make_var("log.rotation.compress", True, cls=tk.BooleanVar), widget_type="check"); r += 1

    # ---------- Tab: Advanced ----------
    def _build_tab_advanced(self):
        frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(frame, text=" 高级 ")
        frame.columnconfigure(1, weight=1)

        r = 0
        ttk.Label(frame, text="── 网关 ──", font=("", 10, "bold")).grid(row=r, column=0, columnspan=3, sticky=tk.W, pady=(0, 5)); r += 1
        self._add_row(frame, r, "响应头超时(秒):", self._make_var("gateway.response_header_timeout", "600")); r += 1
        self._add_row(frame, r, "最大请求体(字节):", self._make_var("gateway.max_body_size", "268435456")); r += 1
        self._add_row(frame, r, "流数据超时(秒):", self._make_var("gateway.stream_data_interval_timeout", "180")); r += 1
        self._add_row(frame, r, "流 Keepalive(秒):", self._make_var("gateway.stream_keepalive_interval", "10")); r += 1
        self._add_row(frame, r, "TLS 指纹伪装:", self._make_var("gateway.tls_fingerprint.enabled", True, cls=tk.BooleanVar), widget_type="check"); r += 1

        ttk.Label(frame, text="── 安全 ──", font=("", 10, "bold")).grid(row=r, column=0, columnspan=3, sticky=tk.W, pady=(15, 5)); r += 1
        self._add_row(frame, r, "URL 白名单:", self._make_var("security.url_allowlist.enabled", False, cls=tk.BooleanVar), widget_type="check"); r += 1
        self._add_row(frame, r, "允许私有主机:", self._make_var("security.url_allowlist.allow_private_hosts", True, cls=tk.BooleanVar), widget_type="check"); r += 1
        self._add_row(frame, r, "允许 HTTP:", self._make_var("security.url_allowlist.allow_insecure_http", True, cls=tk.BooleanVar), widget_type="check"); r += 1

        ttk.Label(frame, text="── 速率限制 ──", font=("", 10, "bold")).grid(row=r, column=0, columnspan=3, sticky=tk.W, pady=(15, 5)); r += 1
        self._add_row(frame, r, "过载冷却(分钟):", self._make_var("rate_limit.overload_cooldown_minutes", "10")); r += 1

    # ---------- Tab: Service Logs ----------
    def _build_tab_logs(self):
        frame = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(frame, text=" 运行日志 ")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        self.log_text = scrolledtext.ScrolledText(frame, wrap=tk.WORD, state=tk.DISABLED,
                                                  font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4",
                                                  insertbackground="white")
        self.log_text.grid(row=0, column=0, sticky="nsew")

        btn_bar = ttk.Frame(frame)
        btn_bar.grid(row=1, column=0, sticky=tk.E, pady=3)
        ttk.Button(btn_bar, text="清空日志", command=self._clear_log).pack(side=tk.RIGHT, padx=3)

    # ------------------------------------------------------------------
    # Status bar
    # ------------------------------------------------------------------
    def _build_statusbar(self):
        sb = ttk.Frame(self, relief=tk.SUNKEN)
        sb.pack(fill=tk.X, side=tk.BOTTOM)
        self.lbl_config_path = ttk.Label(sb, text=f"配置文件: {self.config_path}", padding=3)
        self.lbl_config_path.pack(side=tk.LEFT)

    # ------------------------------------------------------------------
    # Config I/O
    # ------------------------------------------------------------------
    def _get_nested(self, data: dict, dotkey: str, default=""):
        """Get nested value from dict using dot notation."""
        keys = dotkey.split(".")
        cur = data
        for k in keys:
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                return default
        return cur

    def _set_nested(self, data: dict, dotkey: str, value):
        """Set nested value in dict using dot notation."""
        keys = dotkey.split(".")
        cur = data
        for k in keys[:-1]:
            if k not in cur or not isinstance(cur[k], dict):
                cur[k] = {}
            cur = cur[k]
        cur[keys[-1]] = value

    def _load_config(self, path: str):
        """Load config.yaml and populate GUI fields."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            messagebox.showerror("加载失败", f"无法读取配置文件:\n{e}")
            return

        self.config_data = deep_merge(get_default_config(), data)
        self.config_path = path
        self.lbl_config_path.config(text=f"配置文件: {self.config_path}")

        # Populate vars
        for dotkey, var in self.vars.items():
            val = self._get_nested(self.config_data, dotkey, "")
            if isinstance(var, tk.BooleanVar):
                var.set(bool(val))
            else:
                var.set(str(val) if val is not None else "")

        self._append_log(f"[INFO] 已加载配置: {path}\n")

    def _collect_config(self) -> dict:
        """Collect GUI fields back into config dict, preserving unknown keys."""
        data = dict(self.config_data)  # shallow copy top level; deep values mutated in place

        int_keys = {
            "server.port", "database.port", "database.max_open_conns", "database.max_idle_conns",
            "database.conn_max_lifetime_minutes", "database.conn_max_idle_time_minutes",
            "redis.port", "redis.db", "redis.pool_size", "redis.min_idle_conns",
            "jwt.expire_hour", "default.user_concurrency", "default.user_balance",
            "log.rotation.max_size_mb", "log.rotation.max_backups", "log.rotation.max_age_days",
            "gateway.response_header_timeout", "gateway.max_body_size",
            "gateway.stream_data_interval_timeout", "gateway.stream_keepalive_interval",
            "rate_limit.overload_cooldown_minutes",
        }
        float_keys = {"default.rate_multiplier"}
        bool_keys = {k for k, v in self.vars.items() if isinstance(v, tk.BooleanVar)}

        for dotkey, var in self.vars.items():
            raw = var.get()
            if dotkey in bool_keys:
                self._set_nested(data, dotkey, bool(raw))
            elif dotkey in int_keys:
                try:
                    self._set_nested(data, dotkey, int(raw))
                except (ValueError, TypeError):
                    self._set_nested(data, dotkey, 0)
            elif dotkey in float_keys:
                try:
                    self._set_nested(data, dotkey, float(raw))
                except (ValueError, TypeError):
                    self._set_nested(data, dotkey, 0.0)
            else:
                self._set_nested(data, dotkey, str(raw))
        return data

    def _save_config(self):
        """Save current GUI state to config.yaml."""
        data = self._collect_config()
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            self._append_log(f"[INFO] 配置已保存: {self.config_path}\n")
            messagebox.showinfo("保存成功", f"配置已保存到:\n{self.config_path}")
        except Exception as e:
            messagebox.showerror("保存失败", f"无法写入配置文件:\n{e}")

    def _save_config_as(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".yaml",
            filetypes=[("YAML 文件", "*.yaml *.yml"), ("所有文件", "*.*")],
            initialfile=DEFAULT_CONFIG_NAME,
        )
        if path:
            self.config_path = path
            self.lbl_config_path.config(text=f"配置文件: {self.config_path}")
            self._save_config()

    def _open_config_dialog(self):
        path = filedialog.askopenfilename(
            filetypes=[("YAML 文件", "*.yaml *.yml"), ("所有文件", "*.*")]
        )
        if path:
            self._load_config(path)

    def _select_exe(self):
        path = filedialog.askopenfilename(
            filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")]
        )
        if path:
            self.exe_path = path
            self.var_exe.set(path)

    # ------------------------------------------------------------------
    # Service control
    # ------------------------------------------------------------------
    def _start_service(self):
        exe = self.var_exe.get().strip()
        if not exe or not os.path.isfile(exe):
            messagebox.showwarning("启动失败", f"找不到程序: {exe}\n请先选择 sub2api.exe 路径")
            return

        # Auto-save config before start
        self._save_config_silent()

        cwd = os.path.dirname(os.path.abspath(exe))
        env = os.environ.copy()
        # Set DATA_DIR to exe directory so config.yaml is found
        env["DATA_DIR"] = cwd
        # Fix Windows timezone issue: set TZ env so Go can resolve timezone
        tz_val = self.vars.get("timezone")
        if tz_val:
            env["TZ"] = tz_val.get() or "UTC"
        # Point ZONEINFO to Go's bundled tzdata if available
        go_root = os.environ.get("GOROOT", "")
        if go_root:
            zoneinfo = os.path.join(go_root, "lib", "time", "zoneinfo.zip")
            if os.path.isfile(zoneinfo):
                env["ZONEINFO"] = zoneinfo

        try:
            self.process = subprocess.Popen(
                [exe],
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                bufsize=1,
            )
        except Exception as e:
            messagebox.showerror("启动失败", str(e))
            return

        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.btn_restart.config(state=tk.NORMAL)
        self.lbl_status.config(text="● 运行中", foreground="green")
        self._append_log(f"[INFO] 服务已启动 (PID: {self.process.pid})\n")

        # Switch to log tab
        self.notebook.select(self.notebook.tabs()[-1])

        # Start log reader thread
        t = threading.Thread(target=self._read_process_output, daemon=True)
        t.start()

        # Monitor thread
        t2 = threading.Thread(target=self._monitor_process, daemon=True)
        t2.start()

    def _stop_service(self):
        if self.process and self.process.poll() is None:
            self._append_log("[INFO] 正在停止服务...\n")
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            self._on_process_stopped()

    def _restart_service(self):
        self._stop_service()
        self.after(500, self._start_service)

    def _save_config_silent(self):
        """Save without messagebox."""
        data = self._collect_config()
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            self._append_log(f"[INFO] 配置已自动保存: {self.config_path}\n")
        except Exception:
            pass

    def _on_process_stopped(self):
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self.btn_restart.config(state=tk.DISABLED)
        self.lbl_status.config(text="● 已停止", foreground="gray")
        self._append_log("[INFO] 服务已停止\n")
        self.process = None

    def _read_process_output(self):
        """Read stdout from subprocess (runs in thread)."""
        proc = self.process
        if not proc or not proc.stdout:
            return
        try:
            for line in iter(proc.stdout.readline, b""):
                if self._closing:
                    break
                try:
                    text = line.decode("utf-8", errors="replace")
                except Exception:
                    text = str(line)
                self.log_queue.put(text)
        except Exception:
            pass

    def _monitor_process(self):
        """Monitor subprocess exit (runs in thread)."""
        proc = self.process
        if not proc:
            return
        proc.wait()
        if not self._closing:
            self.log_queue.put(f"[INFO] 进程已退出 (code: {proc.returncode})\n")
            self.after(100, self._on_process_stopped)

    # ------------------------------------------------------------------
    # Log display
    # ------------------------------------------------------------------
    def _poll_log(self):
        """Periodically drain the log queue into the text widget."""
        try:
            while True:
                text = self.log_queue.get_nowait()
                self._append_log(text)
        except queue.Empty:
            pass
        if not self._closing:
            self.after(100, self._poll_log)

    def _append_log(self, text: str):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _clear_log(self):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state=tk.DISABLED)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def _on_close(self):
        if self.process and self.process.poll() is None:
            if not messagebox.askyesno("确认退出", "服务仍在运行，退出将停止服务。\n确定退出吗？"):
                return
            self._closing = True
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self._closing = True
        self.destroy()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app = Sub2APIGui()
    app.mainloop()
