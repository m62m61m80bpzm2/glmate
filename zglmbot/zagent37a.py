#!/usr/bin/env python3
"""
ZAgent - Claude Code-like AI Agent TUI
Single-file cross-platform (Windows 11 + Linux) AI agent with tool calling
Version: 26w36a

Architecture: Single-file with clear modular sections (§1-§13)
Each section is self-contained with well-defined boundaries.
"""

import os
import sys
import json
import time
import subprocess
import shutil
import glob as _glob_mod
import platform
import signal
import traceback
import re
import threading
import difflib
import hashlib
import locale
from datetime import datetime
from pathlib import Path

# ════════════════════════════════════════════════════════════════
# §1  META & VERSION
# ════════════════════════════════════════════════════════════════

VERSION = "26w37e"
APP_NAME = "ZAgent"

# ════════════════════════════════════════════════════════════════
# §2  PLATFORM ADAPTER
#     OS detection, shell auto-detection, path/encoding/newline
# ════════════════════════════════════════════════════════════════

IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"
PLATFORM_NAME = platform.system().lower()

def _detect_shell():
    """Detect the best available shell for the current platform.
    Returns (shell_path, shell_args, shell_name)."""
    if IS_WINDOWS:
        # Try PowerShell first, then cmd
        ps_path = shutil.which("pwsh") or shutil.which("powershell")
        if ps_path:
            return ps_path, ["-NoProfile", "-Command"], "powershell"
        return "cmd.exe", ["/c"], "cmd"
    else:
        # Linux/macOS: prefer user's shell, then bash, then sh
        shell = os.environ.get("SHELL", "")
        if shell and os.path.exists(shell):
            name = os.path.basename(shell)
            return shell, ["-c"], name
        for s in ["/bin/bash", "/bin/zsh", "/bin/sh"]:
            if os.path.exists(s):
                return s, ["-c"], os.path.basename(s)
        return "/bin/sh", ["-c"], "sh"

SHELL_PATH, SHELL_ARGS, SHELL_NAME = _detect_shell()

def _normalize_line_endings(text, target=None):
    """Normalize line endings. target='unix' → LF, 'windows' → CRLF, None → native."""
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    if target == 'windows' or (target is None and IS_WINDOWS):
        return text.replace('\n', '\r\n')
    return text

def _detect_encoding(filepath):
    """Detect file encoding. Returns encoding string."""
    try:
        with open(filepath, 'rb') as f:
            raw = f.read(4096)
        if raw.startswith(b'\xef\xbb\xbf'):
            return 'utf-8-sig'
        if raw.startswith(b'\xff\xfe'):
            return 'utf-16-le'
        if raw.startswith(b'\xfe\xff'):
            return 'utf-16-be'
        try:
            raw.decode('utf-8')
            return 'utf-8'
        except UnicodeDecodeError:
            pass
        for enc in ['gbk', 'gb2312', 'gb18030', 'big5', 'shift_jis', 'euc-kr', 'latin-1']:
            try:
                raw.decode(enc)
                return enc
            except (UnicodeDecodeError, LookupError):
                continue
    except Exception:
        pass
    return 'utf-8'

def _safe_path(p):
    """Normalize path for cross-platform compatibility."""
    p = str(p)
    if IS_WINDOWS:
        p = p.replace('/', '\\')
        if len(p) >= 2 and p[1] == ':':
            p = p[0].upper() + p[1:]
    else:
        p = p.replace('\\', '/')
    return os.path.normpath(p)

def _adapt_command_for_shell(cmd):
    """Adapt a shell command for the current platform's shell."""
    if IS_WINDOWS and SHELL_NAME == "cmd":
        # Translate common Unix commands to Windows equivalents
        simple_map = {
            "ls": "dir /b",
            "ls -la": "dir",
            "ls -l": "dir",
            "cat": "type",
            "rm": "del",
            "cp": "copy",
            "mv": "move",
            "mkdir -p": "mkdir",
            "touch": "type nul >",
            "clear": "cls",
            "which": "where",
        }
        cmd_lower = cmd.strip()
        for unix, win in simple_map.items():
            if cmd_lower == unix or cmd_lower.startswith(unix + " "):
                cmd = cmd.replace(unix, win, 1)
                break
        # Replace Unix paths with Windows paths in arguments
        if '/' in cmd and not cmd.startswith('http'):
            parts = cmd.split()
            for i, part in enumerate(parts):
                if '/' in part and not part.startswith('-') and not part.startswith('http'):
                    parts[i] = part.replace('/', '\\')
            cmd = ' '.join(parts)
    elif IS_WINDOWS and SHELL_NAME == "powershell":
        simple_map = {
            "ls": "Get-ChildItem",
            "cat": "Get-Content",
            "rm": "Remove-Item",
            "cp": "Copy-Item",
            "mv": "Move-Item",
            "touch": "New-Item -ItemType File",
            "clear": "Clear-Host",
            "which": "Get-Command",
        }
        cmd_lower = cmd.strip()
        for unix, ps in simple_map.items():
            if cmd_lower == unix or cmd_lower.startswith(unix + " "):
                cmd = cmd.replace(unix, ps, 1)
                break
    return cmd

# ════════════════════════════════════════════════════════════════
# §3  CONFIG LOADER
#     config.json + custom system prompt + permission rules
# ════════════════════════════════════════════════════════════════

_ZAGENT_HOME = os.environ.get("ZAGENT_HOME", str(Path.home() / ".zagent"))
ZAGENT_HOME = Path(_ZAGENT_HOME)
CONV_DIR = ZAGENT_HOME / "conversations"
CONV_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_FILE = ZAGENT_HOME / "input_history"
SCRIPT_DIR = Path(__file__).parent.resolve()
LOCAL_CONFIG = SCRIPT_DIR / "config.json"

# Model capabilities — defines what each model can do
# text: text chat | vision: image understanding | image_gen: image generation
# video_gen: video generation | video: video understanding (multimodal)
# reasoning: deep thinking/reasoning
MODEL_CAPABILITIES = {
    # GLM models
    "glm-4.7-flash":       {"text", "reasoning"},
    "glm-4-flash":         {"text"},
    "glm-4-plus":          {"text"},
    "glm-4-0520":          {"text"},
    "glm-4-air":           {"text"},
    "glm-4-long":          {"text"},
    "glm-4v":              {"text", "vision", "video"},
    "glm-4v-flash":        {"text", "vision", "video"},
    "glm-4.1v-thinking-flash": {"text", "vision", "video", "reasoning"},
    "glm-z1-flash":        {"text", "reasoning"},
    "cogview-3-flash":     {"image_gen"},
    "cogvideox-flash":     {"video_gen"},
    # Agnes AI platform (separate OpenAI-compatible gateway, its own API key)
    "agnes-2.5-flash":       {"text", "vision", "reasoning"},
    "agnes-2.0-flash":       {"text", "vision"},
    "agnes-1.5-flash":       {"text", "vision"},
    "agnes-image-2.1-flash": {"image_gen"},
    "agnes-image-2.0-flash": {"image_gen"},
    "agnes-video-v2.0":      {"video_gen"},
    # DeepSeek models
    "deepseek-chat":       {"text"},
    "deepseek-reasoner":   {"text", "reasoning"},
    # OpenAI models
    "gpt-4o":              {"text", "vision", "video"},
    "gpt-4o-mini":         {"text", "vision", "video"},
    "gpt-4.1":             {"text", "vision", "video"},
    "gpt-4.1-mini":        {"text", "vision", "video"},
    "o3-mini":             {"text", "reasoning"},
    # Qwen models
    "qwen-turbo":          {"text"},
    "qwen-plus":           {"text"},
    "qwen-max":            {"text"},
    "qwq-32b":             {"text", "reasoning"},
    # Doubao models
    "doubao-1.5-pro":      {"text"},
    "doubao-1.5-lite":     {"text"},
    # SiliconFlow models
    "Qwen/Qwen3-235B-A22B": {"text", "reasoning"},
    "deepseek-ai/DeepSeek-V3": {"text"},
    "deepseek-ai/DeepSeek-R1": {"text", "reasoning"},
}

def model_has_capability(model_name, capability):
    """Check if a model has a specific capability."""
    caps = MODEL_CAPABILITIES.get(model_name, set())
    return capability in caps

def is_multimodal_model(model_name):
    """Check if a model supports vision/image understanding."""
    return model_has_capability(model_name, "vision")

# Model provider presets — each has a baseUrl + model list
MODEL_PROVIDERS = {
    "glm": {
        "name": {"zh": "智谱 GLM", "en": "Zhipu GLM"},
        "baseUrl": "https://open.bigmodel.cn/api/paas/v4",
        "models": {
            "glm-4.7-flash": {"zh": "GLM-4.7-Flash (免费MoE, 1 QPS)", "en": "GLM-4.7-Flash (Free MoE, 1 QPS)"},
            "glm-4-flash": {"zh": "GLM-4-Flash (免费, 128K, 72tok/s)", "en": "GLM-4-Flash (Free, 128K, 72tok/s)"},
            "glm-z1-flash": {"zh": "GLM-Z1-Flash (免费推理模型)", "en": "GLM-Z1-Flash (Free reasoning model)"},
            "glm-4v-flash": {"zh": "GLM-4V-Flash (免费视觉多模态)", "en": "GLM-4V-Flash (Free vision multimodal)"},
            "glm-4.1v-thinking-flash": {"zh": "GLM-4.1V-Thinking-Flash (免费视觉推理, 支持视频)", "en": "GLM-4.1V-Thinking-Flash (Free vision+reasoning, video)"},
            "cogview-3-flash": {"zh": "CogView-3-Flash (免费AI生图)", "en": "CogView-3-Flash (Free AI image gen)"},
            "cogvideox-flash": {"zh": "CogVideoX-Flash (免费AI生视频)", "en": "CogVideoX-Flash (Free AI video gen)"},
            "glm-4-plus": {"zh": "GLM-4-Plus (更智能)", "en": "GLM-4-Plus (Smarter)"},
            "glm-4-0520": {"zh": "GLM-4 (标准版)", "en": "GLM-4 (Standard)"},
            "glm-4-air": {"zh": "GLM-4-Air (轻量版)", "en": "GLM-4-Air (Lightweight)"},
            "glm-4-long": {"zh": "GLM-4-Long (长文本)", "en": "GLM-4-Long (Long context)"},
            "glm-4v": {"zh": "GLM-4V (视觉多模态)", "en": "GLM-4V (Vision multimodal)"},
        }
    },
    "deepseek": {
        "name": {"zh": "DeepSeek", "en": "DeepSeek"},
        "baseUrl": "https://api.deepseek.com/v1",
        "models": {
            "deepseek-chat": {"zh": "DeepSeek-V3 (通用对话)", "en": "DeepSeek-V3 (General Chat)"},
            "deepseek-reasoner": {"zh": "DeepSeek-R1 (推理)", "en": "DeepSeek-R1 (Reasoning)"},
        }
    },
    "openai": {
        "name": {"zh": "OpenAI", "en": "OpenAI"},
        "baseUrl": "https://api.openai.com/v1",
        "models": {
            "gpt-4o": {"zh": "GPT-4o (旗舰)", "en": "GPT-4o (Flagship)"},
            "gpt-4o-mini": {"zh": "GPT-4o-mini (轻量)", "en": "GPT-4o-mini (Lightweight)"},
            "gpt-4.1": {"zh": "GPT-4.1 (最新)", "en": "GPT-4.1 (Latest)"},
            "gpt-4.1-mini": {"zh": "GPT-4.1-mini (轻量)", "en": "GPT-4.1-mini (Lightweight)"},
            "o3-mini": {"zh": "o3-mini (推理)", "en": "o3-mini (Reasoning)"},
        }
    },
    "qwen": {
        "name": {"zh": "通义千问 Qwen", "en": "Qwen"},
        "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": {
            "qwen-turbo": {"zh": "Qwen-Turbo (快速)", "en": "Qwen-Turbo (Fast)"},
            "qwen-plus": {"zh": "Qwen-Plus (均衡)", "en": "Qwen-Plus (Balanced)"},
            "qwen-max": {"zh": "Qwen-Max (最强)", "en": "Qwen-Max (Best)"},
            "qwq-32b": {"zh": "QwQ-32B (推理)", "en": "QwQ-32B (Reasoning)"},
        }
    },
    "doubao": {
        "name": {"zh": "豆包 Doubao", "en": "Doubao"},
        "baseUrl": "https://ark.cn-beijing.volces.com/api/v3",
        "models": {
            "doubao-1.5-pro": {"zh": "Doubao-1.5-Pro (智能)", "en": "Doubao-1.5-Pro (Smart)"},
            "doubao-1.5-lite": {"zh": "Doubao-1.5-Lite (轻量)", "en": "Doubao-1.5-Lite (Lightweight)"},
        }
    },
    "siliconflow": {
        "name": {"zh": "SiliconFlow (硅基流动)", "en": "SiliconFlow"},
        "baseUrl": "https://api.siliconflow.cn/v1",
        "models": {
            "Qwen/Qwen3-235B-A22B": {"zh": "Qwen3-235B (免费)", "en": "Qwen3-235B (Free)"},
            "deepseek-ai/DeepSeek-V3": {"zh": "DeepSeek-V3", "en": "DeepSeek-V3"},
            "deepseek-ai/DeepSeek-R1": {"zh": "DeepSeek-R1 (推理)", "en": "DeepSeek-R1 (Reasoning)"},
        }
    },
    "agnes": {
        "name": {"zh": "Agnes AI", "en": "Agnes AI"},
        "baseUrl": "https://apihub.agnes-ai.com/v1",
        # Agnes uses its own API key — configure with: /key ages <YOUR_AGNES_KEY>
        # (providerKeys["agnes"]). Video has a non-OpenAI submit/poll protocol.
        "videoApi": "agnes",   # marker for _tool_video_generate protocol branch
        "models": {
            "agnes-2.5-flash": {"zh": "Agnes-2.5-Flash (512K, 视觉/推理/工具)", "en": "Agnes-2.5-Flash (512K, vision/reasoning/tools)"},
            "agnes-2.0-flash": {"zh": "Agnes-2.0-Flash (256K, 视觉)", "en": "Agnes-2.0-Flash (256K, vision)"},
            "agnes-1.5-flash": {"zh": "Agnes-1.5-Flash (高速)", "en": "Agnes-1.5-Flash (fast)"},
            "agnes-image-2.1-flash": {"zh": "Agnes-Image-2.1-Flash (高质量AI生图/编辑)", "en": "Agnes-Image-2.1-Flash (image gen/edit)"},
            "agnes-image-2.0-flash": {"zh": "Agnes-Image-2.0-Flash (快速AI生图)", "en": "Agnes-Image-2.0-Flash (fast image gen)"},
            "agnes-video-v2.0": {"zh": "Agnes-Video-v2.0 (文/图生视频)", "en": "Agnes-Video-v2.0 (text/image-to-video)"},
        }
    },
}

# Build flat AVAILABLE_MODELS from all providers
AVAILABLE_MODELS = {}
for _prov_id, _prov_info in MODEL_PROVIDERS.items():
    for _model_id, _model_desc in _prov_info["models"].items():
        AVAILABLE_MODELS[_model_id] = _model_desc

# Permissions config file (separate from config.json)
PERM_CONFIG = SCRIPT_DIR / "permissions.json"

# Default permission rules
DEFAULT_PERMISSIONS = {
    "allowRead": True,
    "allowWrite": True,
    "allowDelete": True,
    "allowExecute": True,
    "allowNetwork": True,
    "maxFileReadSize": 50000,
    "maxFileWriteSize": 200000,
    "maxCommandOutputSize": 30000,
    "blockedCommands": [],
    "allowedPaths": [],
    "blockedPaths": [],
    "confirmDangerousCommands": True,
    "maxSubAgents": 3,
    "maxRounds": 20,
}

# Permission name localization
PERM_NAMES = {
    "allowRead": {"zh": "允许读取", "en": "Allow Read"},
    "allowWrite": {"zh": "允许写入", "en": "Allow Write"},
    "allowDelete": {"zh": "允许删除", "en": "Allow Delete"},
    "allowExecute": {"zh": "允许执行", "en": "Allow Execute"},
    "allowNetwork": {"zh": "允许联网", "en": "Allow Network"},
    "maxFileReadSize": {"zh": "最大读取大小", "en": "Max Read Size"},
    "maxFileWriteSize": {"zh": "最大写入大小", "en": "Max Write Size"},
    "maxCommandOutputSize": {"zh": "最大命令输出", "en": "Max Command Output"},
    "blockedCommands": {"zh": "屏蔽命令", "en": "Blocked Commands"},
    "allowedPaths": {"zh": "允许路径", "en": "Allowed Paths"},
    "blockedPaths": {"zh": "屏蔽路径", "en": "Blocked Paths"},
    "confirmDangerousCommands": {"zh": "危险命令需确认", "en": "Confirm Dangerous Commands"},
    "maxSubAgents": {"zh": "最大子代理数", "en": "Max Sub-Agents"},
    "maxRounds": {"zh": "最大轮数", "en": "Max Rounds"},

}

# ── DANGEROUS COMMANDS (require confirmation) ──────────────────
# Windows: delete software/files, system damage, disk partition
# Linux: sudo, root-level operations
DANGEROUS_COMMANDS_WINDOWS = [
    # 卸载/删除软件
    re.compile(r'\b(winget|choco|scoop)\s+uninstall\b', re.IGNORECASE),
    re.compile(r'\bappwiz\.cpl\b', re.IGNORECASE),
    re.compile(r'\bmsiexec\s+/x\b', re.IGNORECASE),
    # 删除系统文件/目录
    re.compile(r'\b(rmdir|rd)\s+(?:/[sq]\s+)*[cC]:\\', re.IGNORECASE),
    re.compile(r'\bdel\s+(?:/[sfq]\s+)*[cC]:\\Windows', re.IGNORECASE),
    re.compile(r'\bRemove-Item\s+.*[cC]:\\Windows', re.IGNORECASE),
    re.compile(r'\bRemove-Item\s+-Recurse\s+-Force\b', re.IGNORECASE),
    # 系统损坏
    re.compile(r'\bsfc\s+/scannow\b', re.IGNORECASE),
    re.compile(r'\bdism\s+/', re.IGNORECASE),
    re.compile(r'\bbcdedit\b', re.IGNORECASE),
    re.compile(r'\breg\s+(delete|add)\b', re.IGNORECASE),
    re.compile(r'\bnetsh\b', re.IGNORECASE),
    re.compile(r'\bservices\.msc\b', re.IGNORECASE),
    re.compile(r'\btaskkill\s+(?:/[fim]\s+)*', re.IGNORECASE),
    re.compile(r'\bStop-Process\s+-Force\b', re.IGNORECASE),
    # 磁盘分区操作
    re.compile(r'\bdiskpart\b', re.IGNORECASE),
    re.compile(r'\bformat\s+[a-zA-Z]:\b', re.IGNORECASE),
    re.compile(r'\bchkdsk\s+(?:/[fxr]\s+)*', re.IGNORECASE),
    re.compile(r'\bGet-Disk\b.*\bClear-Disk\b', re.IGNORECASE),
    re.compile(r'\bNew-Partition\b', re.IGNORECASE),
    re.compile(r'\bRemove-Partition\b', re.IGNORECASE),
    re.compile(r'\bInitialize-Disk\b', re.IGNORECASE),
    # 危险 PowerShell
    re.compile(r'\bSet-ExecutionPolicy\b', re.IGNORECASE),
    re.compile(r'\bEnable-PSRemoting\b', re.IGNORECASE),
]

DANGEROUS_COMMANDS_LINUX = [
    # sudo 及 root 操作
    re.compile(r'\bsudo\b', re.IGNORECASE),
    re.compile(r'\bsu\s+(-|root)\b', re.IGNORECASE),
    re.compile(r'\bsu(?:\s|$)', re.IGNORECASE),
    # 系统级危险操作
    re.compile(r'\brm\s+-rf\s+/', re.IGNORECASE),
    re.compile(r'\bchmod\s+(-R\s+)?777\s+/', re.IGNORECASE),
    re.compile(r'\bchown\s+-R\s+root\s+/', re.IGNORECASE),
    re.compile(r'\bmkfs\.', re.IGNORECASE),
    re.compile(r'\bdd\s+if=', re.IGNORECASE),
    re.compile(r'\bmount\b', re.IGNORECASE),
    re.compile(r'\bumount\b', re.IGNORECASE),
    re.compile(r'\bfdisk\b', re.IGNORECASE),
    re.compile(r'\bparted\b', re.IGNORECASE),
    re.compile(r'\biptables\b', re.IGNORECASE),
    re.compile(r'\bsystemctl\s+(stop|disable|mask)\b', re.IGNORECASE),
    re.compile(r'\bservice\s+\w+\s+stop\b', re.IGNORECASE),
    re.compile(r'\bkillall\b', re.IGNORECASE),
    re.compile(r'\bpkill\s+-9\b', re.IGNORECASE),
    re.compile(r'\bshutdown\b', re.IGNORECASE),
    re.compile(r'\breboot\b', re.IGNORECASE),
    re.compile(r'\binit\s+[06]\b', re.IGNORECASE),
    # 包管理器卸载
    re.compile(r'\b(apt|apt-get)\s+(remove|purge|autoremove)\b', re.IGNORECASE),
    re.compile(r'\byum\s+(remove|erase)\b', re.IGNORECASE),
    re.compile(r'\bdnf\s+remove\b', re.IGNORECASE),
    re.compile(r'\bpacman\s+-R\b', re.IGNORECASE),
]

def is_dangerous_command(cmd):
    """Check if a command is dangerous and requires confirmation."""
    cmd_stripped = cmd.strip()
    patterns = DANGEROUS_COMMANDS_WINDOWS if IS_WINDOWS else DANGEROUS_COMMANDS_LINUX
    for pattern in patterns:
        try:
            if pattern.search(cmd_stripped):
                return True
        except Exception:
            continue
    return False



ZAI_CONFIG_PATHS = [
    LOCAL_CONFIG,
    Path.cwd() / ".z-ai-config",
    Path.home() / ".z-ai-config",
    Path("/etc/.z-ai-config"),
]

def load_zai_config():
    """Load API config from the first available config file."""
    for p in ZAI_CONFIG_PATHS:
        try:
            if p.exists():
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("baseUrl") and data.get("apiKey"):
                    return data
        except Exception:
            continue
    return None

def load_full_config():
    """Load full config from config.json, including system prompt and permissions."""
    try:
        with open(LOCAL_CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _atomic_write_json(path, data, **json_kwargs):
    """Write JSON atomically: dump to a temp file then os.replace().
    A crash mid-write would otherwise truncate/corrupt the target file."""
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, **json_kwargs)
    os.replace(tmp, str(path))

def save_config(data):
    """Save config to config.json (auto-inject version)."""
    if "version" not in data:
        data["version"] = VERSION
    _atomic_write_json(LOCAL_CONFIG, data, indent=2)

def get_system_prompt():
    """Get system prompt from config, or use default."""
    cfg = load_full_config()
    custom = cfg.get("systemPrompt", "")
    if custom and custom.strip():
        return custom.strip()
    return SYSTEM_PROMPT_DEFAULT

def load_permissions():
    """Load permissions from permissions.json (standalone file)."""
    try:
        with open(PERM_CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_permissions(data):
    """Save permissions to permissions.json."""
    _atomic_write_json(PERM_CONFIG, data, indent=2)

def get_permissions():
    """Get permission rules from permissions.json, merged with defaults."""
    perms = dict(DEFAULT_PERMISSIONS)
    custom_perms = load_permissions()
    perms.update(custom_perms)
    return perms

def is_command_allowed(cmd):
    """Check if a command is allowed by permission rules."""
    perms = get_permissions()
    if not perms.get("allowExecute", True):
        return False
    cmd_stripped = cmd.strip().lower()
    for blocked in (perms.get("blockedCommands", []) or []):
        blocked_lower = blocked.lower().strip()
        if not blocked_lower:
            continue
        # Match as a word boundary: must be the command name itself (first word) 
        # or a separate argument
        cmd_words = cmd_stripped.split()
        if cmd_words and cmd_words[0] == blocked_lower:
            return False
        # Also check if blocked appears as a standalone word anywhere
        if re.search(r'\b' + re.escape(blocked_lower) + r'\b', cmd_stripped):
            return False
    return True



def _is_path_under(base_path, target_path):
    """Check if target_path is under base_path (proper path component boundary).
    E.g., /home/user/foo blocks /home/user/foo and /home/user/foo/bar
    but NOT /home/user/foobar"""
    # normcase normalizes case and slash direction (Windows: 'D:\Foo' == 'd:/foo'),
    # otherwise blockedPaths can be bypassed with case/slash variants
    base = os.path.normcase(os.path.abspath(base_path))
    target = os.path.normcase(os.path.abspath(target_path))
    if target == base:
        return True
    # Check if target starts with base + separator (ensures proper component boundary)
    if target.startswith(base + os.sep):
        return True
    return False

def is_path_allowed(filepath):
    """Check if a path is allowed by permission rules."""
    perms = get_permissions()
    allowed = perms.get("allowedPaths", []) or []
    blocked = perms.get("blockedPaths", []) or []
    if blocked:
        abs_path = os.path.abspath(str(filepath))
        for bp in blocked:
            if _is_path_under(bp, abs_path):
                return False
    if allowed:
        abs_path = os.path.abspath(str(filepath))
        for ap in allowed:
            if _is_path_under(ap, abs_path):
                return True
        return False
    return True

# Context paths: auto-load instruction files into system prompt
CONTEXT_PATHS = [
    ".zagent.md", "ZAGENT.md", "zagent.md",
    ".cursorrules", ".claude", "CLAUDE.md",
]

def load_context_instructions():
    """Load instructions from context path files (like CLAUDE.md)."""
    instructions = []
    cwd = os.getcwd()
    for name in CONTEXT_PATHS:
        p = os.path.join(cwd, name)
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if content:
                    instructions.append(f"--- {name} ---\n{content}")
            except Exception:
                continue
    return "\n\n".join(instructions)

# ════════════════════════════════════════════════════════════════
# §4  i18n — MULTI-LANGUAGE SUPPORT
# ════════════════════════════════════════════════════════════════

LANG_DATA = {
    "zh": {
        "banner.help": "/help 帮助 /config 配置 Ctrl+C 退出",
        "banner.api_ok": "API 已配置",
        "banner.api_no": "API未配置 (运行 /config setup 或 python zagent.py setup)",
        "thinking": "🤔 思考中... (第 {n} 轮) ({esc})",        
        "thinking.view_hint": "📄 Ctrl+点击在新终端查看思考过程",
        "continue_process": "🔄 继续处理... ({esc})",
        "max_rounds": "达到最大轮次限制",
        "empty_response": "LLM 返回空响应",
        "tool_call": "🔧 调用工具: {name}({args})",
        "tool_result": "📋 结果: {name}",
        "tool_rate_wait": "限速等待 {time}s...",
        "retry.429.peak": "服务器高峰拥挤 (429)，非账户问题，第{n}次重试，等待{wait}s...",
        "retry.429.limit": "账户访问频繁触发限流 (429)，请放慢使用节奏，第{n}次重试，等待{wait}s...",
        "retry.429.unknown": "请求被拒 (429)，第{n}次重试，等待{wait}s...",
        "retry.conn_fail": "连接失败，第{n}次重试，等待{wait}s... ({reason})",
        "retry.max": "API 调用失败：超过最大重试次数",
        "tool.web_search": "🔍 搜索网络",
        "tool.web_search_result": "🔍 搜索结果",
        "tool.web_search_error": "搜索失败",
        "help.search_desc": "联网搜索",
        "esc.stopped": "⏹ 已停止思考，对话已保存",
        "esc.hint": "按 ESC 停止思考",
        "picker.title": "📋 选择对话",
        "picker.new": "🆕 新建对话",
        "picker.prompt": "输入名字选择 (回车新建): ",
        "picker.input_name": "给对话起个名字: ",
        "picker.invalid": "未找到，请重新输入 (回车新建)",
        "picker.ambiguous": "匹配到多个对话 ({count}个)，请输入更精确的名称",
        "picker.new_hint": "🆕 回车 = 新建对话",
        "picker.loaded": "已打开: {title} ({count} 条消息)",
        "picker.no_convs": "(暂无对话)",
        "cmd.delete_name": "输入对话名称删除 (回车取消): ",
        "cmd.delete_multiple": "匹配到多个对话 ({count}个)，请输入更精确的名称",
        "cmd.delete_not_found": "未找到对话: {name}",
        "help.new": "开始新对话 (可起名)",
        "help.list": "列出已保存的对话",
        "help.open": "打开已保存的对话",
        "help.delete": "删除对话 (交互式选择)",
        "help.rename": "重命名当前对话",
        "help.export": "导出对话为 Markdown",
        "help.sys": "修改系统提示",
        "help.clear": "清屏",
        "help.info": "显示对话信息和记忆存储位置",
        "help.model": "查看/设置模型 (主模型/多模态/备用/子代理)",
        "help.config": "查看/编辑配置",
        "help.lang": "切换语言 (中文/English)",
        "help.quit": "退出 ZAgent",
        "help.bash_desc": "执行跨平台 shell 命令",
        "help.read_desc": "读取文件/目录内容 (带行号)",
        "help.write_desc": "写入文件 (自动换行符)",
        "help.edit_desc": "精准编辑文件 (5种匹配策略)",
        "help.glob_desc": "按模式查找文件",
        "help.grep_desc": "搜索文件内容",
        "help.search_desc": "联网搜索",
        "cmd.new_done": "新对话已开始",
        "cmd.new_named": "新对话已开始: {name}",
        "cmd.no_convs": "没有保存的对话",
        "cmd.conv_saved": "已保存的对话",
        "cmd.opened": "已打开: {title} ({count} 条消息)",
        "cmd.deleted": "已删除: {name}",
        "cmd.delete_which": "输入编号删除 (0=取消): ",
        "cmd.delete_cancel": "已取消删除",
        "cmd.delete_confirm": "确认删除 \"{title}\"? (y/N): ",
        "cmd.delete_done": "已删除: {title}",
        "cmd.exported": "已导出到: {path}",
        "cmd.sys_updated": "系统提示已更新",
        "cmd.sys_prompt": "系统提示",
        "cmd.conv_info": "对话信息",
        "cmd.conv_dir": "记忆存储",
        "cmd.conv_dir_env": "(通过 ZAGENT_HOME 环境变量设置)",
        "cmd.rename_done": "对话已重命名为: {name}",
        "cmd.rename_prompt": "新名称: ",
        "cmd.model_changed": "模型已切换为 {model} ({desc})",
        "cmd.no_config": "未找到配置，请先运行 /config setup",
        "cmd.model_info": "模型信息",
        "cmd.current": "当前",
        "cmd.unknown": "未知命令: {cmd}，输入 /help 查看帮助",
        "cmd.exit_save": "再见！对话已保存。",
        "cmd.exit_nosave": "再见！",
        "cmd.ctrlc_hint": "Ctrl+C. /quit退出, 或按Enter继续",
        "cmd.cd_changed": "工作目录已切换: {path}",
        "cmd.cd_not_found": "目录不存在: {path}",
        "help.cd_desc": "切换工作目录",
        "cmd.exit_confirm": "确认退出? (y/N): ",
        "cmd.perm_denied": "⚠ 操作被权限规则拒绝: {action}",
        "cmd.dangerous": "⚠️ 危险命令! {cmd}",
        "cmd.dangerous_blocked": "⛔ 危险命令已拦截: {cmd}",
        "cmd.dangerous_confirm": "确认执行? (y/N): ",
        "cmd.dangerous_confirmed": "⚠ 已确认执行危险命令",
        "perm.title": "🔒 权限管理",
        "perm.show": "显示当前权限",
        "perm.edit": "编辑权限",
        "perm.reset": "重置为默认权限",
        "perm.file": "权限配置文件",
        "perm.saved": "权限已保存到: {path}",
        "perm.reset_done": "权限已重置为默认值",
        "perm.edit_hint": "输入权限名和值 (例如: allowWrite=false) 或回车结束",
        "perm.edit_prompt": "  权限名=值: ",
        "perm.edit_invalid": "无效格式，请使用: 权限名 on/off/数字/null",
        "perm.bool_section": "📋 允许/禁止",
        "perm.num_section": "📏 大小限制",
        "perm.list_section": "🚫 限制列表",
        "perm.empty": "(空)",
        "perm.unlimited": "∞ 无限",
        "perm.set_on": "开启权限",
        "perm.set_off": "关闭权限",
        "perm.set_num": "设置数值",
        "perm.set_null": "设为无限",
        "banner.perm": "/perm 权限管理",
        "provider.title": "🏭 模型服务商",
        "provider.select": "选择服务商",
        "provider.selected": "已切换到: {name} ({model})",
        "provider.auto_baseUrl": "已自动设置 baseUrl: {url}",
        "provider.custom": "自定义 (OpenAI兼容)",
        "provider.not_found": "未找到模型 {model} 对应的服务商",
        "help.provider": "切换模型服务商",
        "help.models": "列出可用模型",
        "config.title": "配置",
        "config.file": "配置文件",
        "config.no_config": "未找到有效的 API 配置！",
        "config.local_config": "本地配置",
        "config.quick_setup": "快速配置",
        "config.or_edit": "或手动编辑 config.json",
        "config.available_models": "可用模型",
        "config.setup_title": "ZAgent 配置向导",
        "config.will_save": "配置将保存到",
        "config.api_url": "API Base URL",
        "config.api_key": "API Key",
        "config.api_key_required": "已取消: API Key 是必填项",
        "config.model": "模型",
        "config.optional": "(可选，回车跳过)",
        "config.saved": "配置已保存到: {path}",
        "config.conn_test": "正在测试连接...",
        "config.conn_ok": "连接成功！",
        "config.conn_fail": "连接测试失败: {err}",
        "config.test_title": "测试连接",
        "config.search_order": "配置文件搜索顺序",
        "config.active": "激活",
        "config.default": "默认",
        "config.help_title": "配置帮助",
        "config.show": "显示当前配置",
        "config.setup": "交互式配置向导",
        "config.test": "测试 API 连接",
        "config.path": "显示配置文件位置",
        "config.models": "列出可用模型",
        "config.lang": "语言",
        "config.lang_changed": "语言已切换为: {lang}",
        "help.title": "帮助",
        "help.commands": "命令列表",
        "help.quick_tools": "快捷工具",
        "help.coding_tools": "编码工具",
        "restored": "已恢复: {title} ({count} 条消息)",
        "llm.error": "LLM 错误: {err}",
        "llm.no_config": "未找到 API 配置，请先运行 /config setup",
        "llm.invalid_config": "配置无效: baseUrl 和 apiKey 为必填项",
        "llm.parse_error": "解析 API 响应失败: {err}",
        "edit.strategy_exact": "精确匹配",
        "edit.strategy_line": "行号匹配",
        "edit.strategy_prefix_suffix": "前后缀匹配",
        "edit.strategy_regex": "正则匹配",
        "edit.strategy_fuzzy": "模糊匹配",
        "edit.not_found": "未找到匹配内容 ({strategy})",
        "edit.multiple_matches": "匹配到多处 ({count}处)，请提供更多上下文",
        "edit.success": "编辑成功 ({strategy})，替换 {count} 处",
        "edit.read_first": "请先读取文件再编辑 (安全检查)",
        "edit.no_changes": "内容无变化，跳过编辑",
        "thinking.on": "思考模式: 强制思考",
        "thinking.off": "思考模式: 自动",
        "thinking.current": "当前思考模式",
        "thinking.process": "💭 思考过程 ({time}s)",
        "context.compressed": "📦 上下文已压缩 (保留最近 {n} 轮对话)",
        "context.compressed_tok": "📦 上下文已压缩 (当前 ~{tokens} tokens)",
        "context.compressing": "📦 正在压缩上下文...",
        "context.manual": "📦 手动压缩上下文...",
        "context.manual_done": "📦 上下文已手动压缩 (保留最近 {n} 轮对话)",
        "context.manual_done_tok": "📦 上下文已手动压缩 (当前 ~{tokens} tokens)",
        "thinkkeep.usage": "思维链保留模式: /keepthink on|off | /thinkmodel <模型>",
        "thought.disabled": "思维链保留未开启 — 请先执行 /keepthink on",
        "thought.empty": "当前对话还没有记录任何思考",
        "thought.title": "当前思维链",
        "thought.summary": "已压缩摘要",
        "thought.pending": "未压缩思考",
        "thinkkeep.on": "🧠 思维链保留模式已开启（思维将跨轮注入LLM，累计{trig} tok后台压缩，单轮{force} tok强制压缩）",
        "thinkkeep.off": "🧠 思维链保留模式已关闭",
        "thinkkeep.status_on": "开启",
        "thinkkeep.status_off": "关闭",
        "thinkkeep.model": "思维压缩模型",
        "thinkmodel.set": "思维压缩模型已设置为: {model}",
        "thinking.compressing_bg": "🧠 累计思维达到 {tokens} tokens，后台压缩中（不阻塞对话，下一轮自动等待合并）...",
        "thinking.compressing_sync": "🧠 正在同步压缩思维链（本次需等待）...",
        "thinking.compressed_sync": "🧠 思维链压缩完成 (当前 ~{tokens} tokens)",
        "thinking.compress_report": "🧠 思维压缩报告: 原文 ~{before} tok → 摘要 ~{after} tok (点此查看原文与摘要)",
        "thinking.force_stop": "单轮思考超过 {limit} tokens，已强制停止本轮生成，压缩思维后继续...",
        "quiet.on": "🔇 工作输出隐藏模式已开启（工作过程只显示状态条，仅最终回答显示在主界面）",
        "quiet.off": "🔊 工作输出隐藏模式已关闭（工具结果直接显示）",
        "quiet.status_on": "开启",
        "quiet.status_off": "关闭",
        "quiet.working": "工作中: {name}",
        "quiet.done": "🔧 {name} 完成，输出已收起",
        "quiet.view": "📄 点此查看完整工作输出",
        "quiet.worklog": "本轮工作输出",
        "quiet.worked": "已工作 {time}",
        "quiet.workstart": "开始工作",
        "quiet.turn_done": "本轮工作完成",
        "quiet.think_log": "💭 第 {n} 轮思考过程",
        "key.usage": "多API Key管理: /key | /key <服务商ID或模型名> <key> | /key del <服务商ID或模型名>",
        "key.list_title": "🔑 API Key 配置",
        "key.default": "默认 (baseUrl)",
        "key.set_provider": "已为服务商 {target} 设置独立 API Key",
        "key.set_model": "已为模型 {target} 设置独立 API Key",
        "key.deleted": "已删除 {target} 的独立 API Key",
        "key.not_found": "未找到 {target} 的独立 Key（它可能就是默认 Key）",
        "key.bad_format": "格式错误。用法: /key <服务商ID或模型名> <apiKey>",
        "subagent.running": "⠋ 子代理: {desc}",
        "subagent.completed": "✅ 子代理完成: {desc}",
        "subagent.failed": "❌ 子代理失败: {desc}",
        "subagent.disabled": "子代理已禁用 (maxSubAgents=0)",
        "subagent.queued": "⏳ 子代理排队中: {desc} (并发限制)",
        "subagent.log": "📄 子代理日志: {path}",
        "subagent.view_hint": "Ctrl+点击在新终端查看子代理详情",
        "help.thinking": "切换思考模式",
        "help.compress": "手动压缩上下文 (LLM决定保留记忆)",
        "help.logs": "查看思考/子代理日志",
        "help.clipboard": "从剪贴板粘贴输入 (避免多行直接发送)",
        "clipboard.empty": "📋 剪贴板为空",
        "clipboard.pasted": "📋 已粘贴 {n} 行内容",
        "clipboard.error": "📋 读取剪贴板失败: {err}",
        # Multi-model / fallback
        "model.switch_backup": "⚠️ 主模型 {model} 报错，切换到备用模型 {backup}",
        "model.switch_back": "✅ 备用模型运行正常，60秒后切回主模型 {model}",
        "model.no_backup_retry": "⚠️ 主模型 {model} 报错 (无备用模型)，持续重试中...",
        "model.retry_before_backup": "⚠️ 主模型 {model} 报错，重试中 ({count}/{max}，{left}次后切换备用)...",
        "model.backup_also_failed": "❌ 备用模型 {backup} 也报错，停止重试",
        "model.fallback_non429": "⚠️ 主模型 {model} 连续{count}次报错，切换备用模型 {backup}",
        # Image/Video tools
        "image_view.using_multimodal": "🔍 使用多模态模型 {model} 分析图片",
        "image_view.no_multimodal": "❌ 没有可用的多模态模型来分析图片 (主模型不支持视觉，也未配置多模态模型)",
        "image_view.no_image": "❌ 未指定图片路径或URL",
        "image_view.result_title": "🖼️ 图片分析结果",
        "image_gen.generating": "🎨 正在生成图片...",
        "image_gen.success": "✅ 图片已生成: {path}",
        "image_gen.fail": "❌ 图片生成失败: {err}",
        "image_gen.no_model": "❌ 没有可用的图片生成模型",
        "video_gen.generating": "🎬 正在生成视频...",
        "video_gen.polling": "🎬 视频生成中... ({status})",
        "video_gen.success": "✅ 视频已生成: {path}",
        "video_gen.fail": "❌ 视频生成失败: {err}",
        "video_gen.no_model": "❌ 没有可用的视频生成模型",
        "video_gen.timeout": "❌ 视频生成超时",
        # Video view
        "video_view.using_multimodal": "🎬 使用多模态模型 {model} 分析视频",
        "video_view.no_multimodal": "❌ 没有可用的多模态模型来分析视频 (主模型不支持视频理解，也未配置多模态模型)",
        "video_view.no_video": "❌ 未指定视频路径或URL",
        "video_view.result_title": "🎬 视频分析结果",
        "video_view.video_too_large": "⚠️ 视频文件过大 ({size}MB)，尝试使用URL方式...",
        "video_view.file_not_found": "❌ 视频文件不存在: {path}",
        # Sub-agent model
        "subagent.model_unknown": "⚠️ 未知子代理模型: {model}，使用默认模型",
    },
    "en": {
        "banner.help": "/help help /config setup Ctrl+C exit",
        "banner.api_ok": "API configured",
        "banner.api_no": "API not configured (run /config setup or python zagent.py setup)",
        "thinking": "🤔 Thinking... (round {n}) ({esc})",
        "thinking.view_hint": "📄 Ctrl+Click to view thinking in new terminal",
        "continue_process": "🔄 Continuing... ({esc})",
        "max_rounds": "Max rounds reached",
        "empty_response": "Empty response from LLM",
        "tool_call": "🔧 Calling tool: {name}({args})",
        "tool_result": "📋 Result: {name}",
        "tool_rate_wait": "Rate limit wait {time}s...",
        "retry.429.peak": "Server peak load (429), not your account issue, retry #{n}, waiting {wait}s...",
        "retry.429.limit": "Account access too frequent (429), slow down, retry #{n}, waiting {wait}s...",
        "retry.429.unknown": "Request rejected (429), retry #{n}, waiting {wait}s...",
        "retry.conn_fail": "Connection failed, retry #{n}, waiting {wait}s... ({reason})",
        "retry.max": "API call failed: max retries exceeded",
        "tool.web_search": "🔍 Search the web",
        "tool.web_search_result": "🔍 Search results",
        "tool.web_search_error": "Search failed",
        "help.search_desc": "Web search",
        "esc.stopped": "⏹ Thinking stopped, conversation saved",
        "esc.hint": "Press ESC to stop thinking",
        "picker.title": "📋 Select Conversation",
        "picker.new": "🆕 New Conversation",
        "picker.prompt": "Enter name (Enter=new): ",
        "picker.input_name": "Name this conversation: ",
        "picker.invalid": "Not found, try again (Enter=new)",
        "picker.ambiguous": "Multiple matches ({count}), be more specific",
        "picker.new_hint": "🆕 Enter = new conversation",
        "picker.loaded": "Opened: {title} ({count} messages)",
        "picker.no_convs": "(No conversations yet)",
        "cmd.delete_name": "Enter conversation name to delete (Enter to cancel): ",
        "cmd.delete_multiple": "Multiple matches ({count}), please be more specific",
        "cmd.delete_not_found": "Conversation not found: {name}",
        "help.new": "Start new conversation (with name)",
        "help.list": "List saved conversations",
        "help.open": "Open a saved conversation",
        "help.delete": "Delete conversation (interactive)",
        "help.rename": "Rename current conversation",
        "help.export": "Export conversation as Markdown",
        "help.sys": "Change system prompt",
        "help.clear": "Clear screen",
        "help.info": "Show conversation info & memory location",
        "help.model": "Show/set models (main/mm/backup/sub)",
        "help.config": "Show/edit config",
        "help.lang": "Switch language (中文/English)",
        "help.quit": "Exit ZAgent",
        "help.bash_desc": "Execute cross-platform shell commands",
        "help.read_desc": "Read file/directory content (with line numbers)",
        "help.write_desc": "Write file (auto line endings)",
        "help.edit_desc": "Precise file edit (5 matching strategies)",
        "help.glob_desc": "Find files by pattern",
        "help.grep_desc": "Search file contents",
        "help.search_desc": "Web search",
        "cmd.new_done": "New conversation started",
        "cmd.new_named": "New conversation: {name}",
        "cmd.no_convs": "No saved conversations",
        "cmd.conv_saved": "Saved Conversations",
        "cmd.opened": "Opened: {title} ({count} messages)",
        "cmd.deleted": "Deleted: {name}",
        "cmd.delete_which": "Enter number to delete (0=cancel): ",
        "cmd.delete_cancel": "Delete cancelled",
        "cmd.delete_confirm": "Delete \"{title}\"? (y/N): ",
        "cmd.delete_done": "Deleted: {title}",
        "cmd.exported": "Exported to: {path}",
        "cmd.sys_updated": "System prompt updated",
        "cmd.sys_prompt": "System Prompt",
        "cmd.conv_info": "Conversation Info",
        "cmd.conv_dir": "Memory storage",
        "cmd.conv_dir_env": "(Set via ZAGENT_HOME env var)",
        "cmd.rename_done": "Conversation renamed to: {name}",
        "cmd.rename_prompt": "New name: ",
        "cmd.model_changed": "Model changed to {model} ({desc})",
        "cmd.no_config": "No config found. Run /config setup first",
        "cmd.model_info": "Model Info",
        "cmd.current": "current",
        "cmd.unknown": "Unknown command: {cmd}, type /help for help",
        "cmd.exit_save": "Bye! Conversation saved.",
        "cmd.exit_nosave": "Bye!",
        "cmd.ctrlc_hint": "Ctrl+C. /quit to exit, or Enter to continue",
        "cmd.cd_changed": "Working directory changed: {path}",
        "cmd.cd_not_found": "Directory not found: {path}",
        "help.cd_desc": "Change working directory",
        "cmd.exit_confirm": "Exit? (y/N): ",
        "cmd.perm_denied": "⚠ Action denied by permission rules: {action}",
        "cmd.dangerous": "⚠️ Dangerous command! {cmd}",
        "cmd.dangerous_blocked": "⛔ Dangerous command blocked: {cmd}",
        "cmd.dangerous_confirm": "Confirm execution? (y/N): ",
        "cmd.dangerous_confirmed": "⚠ Confirmed dangerous command execution",
        "perm.title": "🔒 Permission Management",
        "perm.show": "Show current permissions",
        "perm.edit": "Edit permissions",
        "perm.reset": "Reset to default permissions",
        "perm.file": "Permission config file",
        "perm.saved": "Permissions saved to: {path}",
        "perm.reset_done": "Permissions reset to defaults",
        "perm.edit_hint": "Enter permission name and value (e.g. allowWrite=false) or Enter to finish",
        "perm.edit_prompt": "  perm=value: ",
        "perm.edit_invalid": "Invalid format, use: name on/off/number/null",
        "perm.bool_section": "📋 Allow/Deny",
        "perm.num_section": "📏 Size Limits",
        "perm.list_section": "🚫 Restriction Lists",
        "perm.empty": "(empty)",
        "perm.unlimited": "∞ Unlimited",
        "perm.set_on": "Enable permission",
        "perm.set_off": "Disable permission",
        "perm.set_num": "Set number value",
        "perm.set_null": "Set to unlimited",
        "banner.perm": "/perm permission management",
        "provider.title": "🏭 Model Provider",
        "provider.select": "Select provider",
        "provider.selected": "Switched to: {name} ({model})",
        "provider.auto_baseUrl": "Auto-set baseUrl: {url}",
        "provider.custom": "Custom (OpenAI-compatible)",
        "provider.not_found": "No provider found for model {model}",
        "help.provider": "Switch model provider",
        "help.models": "List available models",
        "config.title": "Configuration",
        "config.file": "Config file",
        "config.no_config": "No valid API config found!",
        "config.local_config": "Local config",
        "config.quick_setup": "Quick Setup",
        "config.or_edit": "Or edit config.json manually",
        "config.available_models": "Available models",
        "config.setup_title": "ZAgent Config Setup",
        "config.will_save": "Config will be saved to",
        "config.api_url": "API Base URL",
        "config.api_key": "API Key",
        "config.api_key_required": "Cancelled: API Key is required",
        "config.model": "Model",
        "config.optional": "(optional, Enter to skip)",
        "config.saved": "Config saved to: {path}",
        "config.conn_test": "Testing connection...",
        "config.conn_ok": "Connection OK!",
        "config.conn_fail": "Connection test failed: {err}",
        "config.test_title": "Test Connection",
        "config.search_order": "Config file search order",
        "config.active": "active",
        "config.default": "default",
        "config.help_title": "Config Help",
        "config.show": "Show current config",
        "config.setup": "Interactive setup wizard",
        "config.test": "Test API connection",
        "config.path": "Show config file locations",
        "config.models": "List available models",
        "config.lang": "Language",
        "config.lang_changed": "Language changed to: {lang}",
        "help.title": "Help",
        "help.commands": "Commands",
        "help.quick_tools": "Quick Tools",
        "help.coding_tools": "Coding Tools",
        "restored": "Restored: {title} ({count} messages)",
        "llm.error": "LLM Error: {err}",
        "llm.no_config": "No API config found. Run /config setup first.",
        "llm.invalid_config": "Invalid config: baseUrl and apiKey are required.",
        "llm.parse_error": "Failed to parse API response: {err}",
        "edit.strategy_exact": "exact match",
        "edit.strategy_line": "line match",
        "edit.strategy_prefix_suffix": "prefix-suffix match",
        "edit.strategy_regex": "regex match",
        "edit.strategy_fuzzy": "fuzzy match",
        "edit.not_found": "No match found ({strategy})",
        "edit.multiple_matches": "Multiple matches ({count}), provide more context",
        "edit.success": "Edit successful ({strategy}), replaced {count} occurrence(s)",
        "edit.read_first": "Read file before editing (safety check)",
        "edit.no_changes": "No changes detected, skipping edit",
        "thinking.on": "Thinking mode: Force thinking",
        "thinking.off": "Thinking mode: Auto",
        "thinking.current": "Current thinking mode",
        "thinking.process": "💭 Thinking ({time}s)",
        "context.compressed": "📦 Context compressed (kept last {n} rounds)",
        "context.compressed_tok": "📦 Context compressed (now ~{tokens} tokens)",
        "context.compressing": "📦 Compressing context...",
        "context.manual": "📦 Manual context compression...",
        "context.manual_done": "📦 Context manually compressed (kept last {n} rounds)",
        "context.manual_done_tok": "📦 Context manually compressed (now ~{tokens} tokens)",
        "thinkkeep.usage": "Chain-of-thought retention: /keepthink on|off | /thinkmodel <model>",
        "thought.disabled": "Chain-of-thought retention is OFF — run /keepthink on first",
        "thought.empty": "No thoughts recorded in this conversation yet",
        "thought.title": "Current chain of thought",
        "thought.summary": "Compressed summary",
        "thought.pending": "Raw thoughts",
        "thinkkeep.on": "🧠 Chain-of-thought retention ON (thoughts injected across rounds; background compression at {trig} tokens, force-stop at {force} tokens per round)",
        "thinkkeep.off": "🧠 Chain-of-thought retention OFF",
        "thinkkeep.status_on": "ON",
        "thinkkeep.status_off": "OFF",
        "thinkkeep.model": "Thinking compression model",
        "thinkmodel.set": "Thinking compression model set to: {model}",
        "thinking.compressing_bg": "🧠 Cumulative thoughts reached {tokens} tokens, compressing in background (non-blocking; merged next round)...",
        "thinking.compressing_sync": "🧠 Compressing chain of thought synchronously (this round waits)...",
        "thinking.compressed_sync": "🧠 Chain-of-thought compressed (now ~{tokens} tokens)",
        "thinking.compress_report": "🧠 Thought compression report: raw ~{before} tok → summary ~{after} tok (click to view original & summary)",
        "thinking.force_stop": "Single-round thinking exceeded {limit} tokens, generation force-stopped; compressing thoughts before continuing...",
        "quiet.on": "🔇 Quiet tool output ON (single working status line; only the final answer shows inline)",
        "quiet.off": "🔊 Quiet tool output OFF (tool results shown inline)",
        "quiet.status_on": "ON",
        "quiet.status_off": "OFF",
        "quiet.working": "Working: {name}",
        "quiet.done": "🔧 {name} done, output collapsed",
        "quiet.view": "📄 Click to view full work output",
        "quiet.worklog": "Work output for this turn",
        "quiet.worked": "Worked {time}",
        "quiet.workstart": "Work started",
        "quiet.turn_done": "Work finished",
        "quiet.think_log": "💭 Round {n} thinking log",
        "key.usage": "Multi API-key: /key | /key <providerId|modelName> <key> | /key del <providerId|modelName>",
        "key.list_title": "🔑 API Key Configuration",
        "key.default": "Default (baseUrl)",
        "key.set_provider": "Dedicated API key set for provider {target}",
        "key.set_model": "Dedicated API key set for model {target}",
        "key.deleted": "Dedicated API key for {target} deleted",
        "key.not_found": "No dedicated key found for {target} (it may use the default key)",
        "key.bad_format": "Bad format. Usage: /key <providerId|modelName> <apiKey>",
        "subagent.running": "⠋ Sub-agent: {desc}",
        "subagent.completed": "✅ Sub-agent completed: {desc}",
        "subagent.failed": "❌ Sub-agent failed: {desc}",
        "subagent.disabled": "Sub-agents disabled (maxSubAgents=0)",
        "subagent.queued": "⏳ Sub-agent queued: {desc} (concurrency limit)",
        "subagent.log": "📄 Sub-agent log: {path}",
        "subagent.view_hint": "Ctrl+Click to view sub-agent in new terminal",
        "help.thinking": "Toggle thinking mode",
        "help.compress": "Manually compress context (LLM decides what to keep)",
        "help.logs": "View thinking/sub-agent logs",
        "help.clipboard": "Paste from clipboard (avoids multi-line auto-send)",
        "clipboard.empty": "📋 Clipboard is empty",
        "clipboard.pasted": "📋 Pasted {n} lines from clipboard",
        "clipboard.error": "📋 Failed to read clipboard: {err}",
        # Multi-model / fallback
        "model.switch_backup": "⚠️ Main model {model} errored, switching to backup {backup}",
        "model.switch_back": "✅ Backup model OK, switching back to {model} in 60s",
        "model.no_backup_retry": "⚠️ Main model {model} errored (no backup), keep retrying...",
        "model.retry_before_backup": "⚠️ Main model {model} errored, retrying ({count}/{max}, switching to backup after {left} more)...",
        "model.backup_also_failed": "❌ Backup model {backup} also errored, stopping",
        "model.fallback_non429": "⚠️ Main model {model} errored {count} times, switching to backup {backup}",
        # Image/Video tools
        "image_view.using_multimodal": "🔍 Using multimodal model {model} to analyze image",
        "image_view.no_multimodal": "❌ No multimodal model available (main model doesn't support vision, no multimodal model configured)",
        "image_view.no_image": "❌ No image path or URL specified",
        "image_view.result_title": "🖼️ Image Analysis Result",
        "image_gen.generating": "🎨 Generating image...",
        "image_gen.success": "✅ Image generated: {path}",
        "image_gen.fail": "❌ Image generation failed: {err}",
        "image_gen.no_model": "❌ No image generation model available",
        "video_gen.generating": "🎬 Generating video...",
        "video_gen.polling": "🎬 Video generating... ({status})",
        "video_gen.success": "✅ Video generated: {path}",
        "video_gen.fail": "❌ Video generation failed: {err}",
        "video_gen.no_model": "❌ No video generation model available",
        "video_gen.timeout": "❌ Video generation timed out",
        # Video view
        "video_view.using_multimodal": "🎬 Using multimodal model {model} to analyze video",
        "video_view.no_multimodal": "❌ No multimodal model available (main model doesn't support video, no multimodal model configured)",
        "video_view.no_video": "❌ No video path or URL specified",
        "video_view.result_title": "🎬 Video Analysis Result",
        "video_view.video_too_large": "⚠️ Video file too large ({size}MB), trying URL mode...",
        "video_view.file_not_found": "❌ Video file not found: {path}",
        # Sub-agent model
        "subagent.model_unknown": "⚠️ Unknown sub-agent model: {model}, using default",
    },
}

def _get_lang():
    """Get current language from config.json."""
    try:
        with open(LOCAL_CONFIG, "r", encoding="utf-8") as f:
            data = json.load(f)
        lang = data.get("lang", "zh")
        if lang in LANG_DATA:
            return lang
    except Exception:
        pass
    return "zh"

def t(key, **kwargs):
    """Translate a key to the current language."""
    if key is None or not isinstance(key, str):
        return key
    lang = _get_lang()
    text = LANG_DATA.get(lang, LANG_DATA["zh"]).get(key, key)
    if text is None:
        text = key
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError, AttributeError):
            pass
    return text

def model_desc(model_name, lang=None):
    """Get model description in current language."""
    if model_name is None or not isinstance(model_name, str):
        return str(model_name) if model_name is not None else ""
    if lang is None:
        lang = _get_lang()
    info = AVAILABLE_MODELS.get(model_name, {})
    if isinstance(info, dict):
        return info.get(lang, info.get("zh", model_name))
    return str(info)

def find_provider_for_model(model_name):
    """Find which provider a model belongs to. Returns (provider_id, provider_info) or (None, None)."""
    for prov_id, prov_info in MODEL_PROVIDERS.items():
        if model_name in prov_info["models"]:
            return prov_id, prov_info
    return None, None

def get_model_capabilities(model_name):
    """Return capability set for a model.
    Registered models use MODEL_CAPABILITIES; ANY custom/unknown model name
    defaults to {"text"} so users can fill in any model name + key and use it."""
    if not model_name or not isinstance(model_name, str):
        return {"text"}
    return MODEL_CAPABILITIES.get(model_name, {"text"})

def resolve_model_endpoint(model_name, full_config=None):
    """Resolve (base_url, api_key) for a given model.

    Multi-key priority (26w35a+):
      1. config["modelKeys"][model]      — a key bound to one exact model
      2. config["providerKeys"][provId]  — a key bound to the model's provider
      3. default config["baseUrl"]/["apiKey"] (custom unregistered models live here)

    Returns ("", "") when nothing is configured.
    """
    if full_config is None:
        full_config = load_full_config()
    base_url = (full_config.get("baseUrl") or "").rstrip("/")
    api_key = full_config.get("apiKey", "")
    if not model_name:
        return base_url, api_key
    model_keys = full_config.get("modelKeys") or {}
    if model_name in model_keys and model_keys[model_name]:
        return base_url, model_keys[model_name]
    prov_id, prov_info = find_provider_for_model(model_name)
    if prov_id:
        provider_keys = full_config.get("providerKeys") or {}
        prov_key = provider_keys.get(prov_id)
        if prov_key:
            return prov_info["baseUrl"].rstrip("/"), prov_key
        return prov_info["baseUrl"].rstrip("/"), api_key
    return base_url, api_key

def _default_provider_id(full_config=None):
    """Which registered provider the DEFAULT baseUrl/apiKey belongs to (or None)."""
    if full_config is None:
        full_config = load_full_config()
    default_base = (full_config.get("baseUrl") or "").rstrip("/")
    for pid, pinfo in MODEL_PROVIDERS.items():
        if pinfo["baseUrl"].rstrip("/") == default_base:
            return pid
    return None

def provider_has_dedicated_key(model_name, full_config=None):
    """True when the model's provider (or the exact model) is coverable:
    modelKeys[model] set, or providerKeys[provider] set, or the model's provider
    is the same provider the DEFAULT key belongs to. Otherwise calls would use a
    foreign-platform key and get 401 — used for setup hints."""
    if full_config is None:
        full_config = load_full_config()
    if (full_config.get("modelKeys") or {}).get(model_name):
        return True
    prov_id, _ = find_provider_for_model(model_name)
    if prov_id is None:
        return True  # unregistered custom model on the default endpoint
    if (full_config.get("providerKeys") or {}).get(prov_id):
        return True
    return prov_id == _default_provider_id(full_config)

# ── Chain-of-thought retention settings (26w35a+) ──────────────
def get_main_context_window():
    """Main model's context window (tokens); falls back to 128K for unknown models."""
    try:
        model = load_full_config().get("model", "")
    except Exception:
        model = ""
    return MODEL_CONTEXT_WINDOWS.get(model, 128000)

def get_thinking_compress_trigger_tokens():
    """Cumulative uncompressed thoughts → background compression (10% of main model's context window)."""
    return get_main_context_window() // 10

def get_thinking_single_round_force_tokens():
    """One round over this → force stop + sync compression (20% of main model's context window)."""
    return get_main_context_window() // 5

def is_keep_thinking_enabled():
    """Config option: keepThinking — retain AI reasoning across rounds."""
    return bool(load_full_config().get("keepThinking", False))

def set_keep_thinking(enabled):
    cfg = load_full_config()
    cfg["keepThinking"] = bool(enabled)
    save_config(cfg)

def get_thinking_compress_model():
    """Model used to compress accumulated chain-of-thought. Falls back to main model."""
    cfg = load_full_config()
    return cfg.get("thinkingCompressModel") or cfg.get("model", "glm-4-flash")

def set_thinking_compress_model(model_name):
    cfg = load_full_config()
    cfg["thinkingCompressModel"] = model_name
    save_config(cfg)

# ── Quiet-tools / hide work output setting (26w36a+) ───────────
def is_quiet_tools_enabled():
    """When ON, tool execution shows only a spinner + a clickable work-log link;
    raw tool outputs stay out of the main TUI. Only the final answer renders."""
    return bool(load_full_config().get("hideWorkOutput", False))

def set_quiet_tools(enabled):
    cfg = load_full_config()
    cfg["hideWorkOutput"] = bool(enabled)
    save_config(cfg)

# Per-turn tool work logs (hidden-output mode)
WORK_LOG_DIR = ZAGENT_HOME / "work"
WORK_LOG_DIR.mkdir(parents=True, exist_ok=True)

def _spin_while(fn, label=None, *args, show_spinner=True, **kwargs):
    """Run fn(*args, **kwargs) in a worker thread while showing a braille spinner
    on stderr. Returns fn's result. ESC-aware: pressing ESC aborts active HTTP
    sockets, returns the sentinel "__ESC_STOP__" immediately and leaves the
    tool thread running in the background (daemon) so the UI reacts at once.
    show_spinner=False runs silently (used when the global work status line
    already owns the display in quiet mode)."""
    box = {}
    def _worker():
        try:
            box["result"] = fn(*args, **kwargs)
        except Exception as ex:
            box["error"] = ex
    th = threading.Thread(target=_worker, daemon=True)
    th.start()
    frame_idx = 0
    interrupted = False
    while th.is_alive():
        if _check_esc_stop():
            interrupted = True
            _abort_active_responses()  # unblock HTTP-based tools (video/image poll etc.)
            break
        if show_spinner:
            frame = SPIN_FRAMES[frame_idx % len(SPIN_FRAMES)]
            sys.stderr.write(f"\r{frame} {label}... ")
            sys.stderr.flush()
            frame_idx += 1
        time.sleep(0.08)
    if show_spinner:
        sys.stderr.write("\r" + " " * 80 + "\r")
        sys.stderr.flush()
    if interrupted:
        return "__ESC_STOP__"
    th.join()
    if "error" in box:
        raise box["error"]
    return box.get("result")

# ── Work status line (quiet mode, 26w37b) ──────────────────────
# Module-level hook so call_llm_direct / tools can talk to the status line
# and the work log without needing agent_loop's locals:
_CURRENT_WORK_STATUS = None   # _WorkStatus instance of the running quiet turn
_QUIET_WORK_LOG = None        # _work_log_append function of the running quiet turn

def _quiet_log_msg(msg):
    """Route a status/warning message (429 retries, fallbacks, rate limits...)
    into the current turn's work log. Returns True if quiet mode consumed it."""
    if _QUIET_WORK_LOG is None:
        return False
    try:
        _QUIET_WORK_LOG(f"\n⚠️ {msg}\n")
        return True
    except Exception:
        return False

def _osc8_link(file_path, label):
    """Raw ANSI OSC 8 hyperlink for direct stderr writes (Ctrl+Click in
    Windows Terminal opens the file). Plain label as fallback."""
    try:
        uri = Path(file_path).resolve().as_uri()
        return f"\x1b]8;;{uri}\x07{label}\x1b]8;;\x07"
    except Exception:
        return label

class _WorkStatus:
    """IDE-style single status line on stderr for the whole work phase:
    '⠋ 工作中 · 1m23s · 第3轮 · 💭 思考中 ~1234 tok'
    - The whole line is an OSC 8 hyperlink to the work log (Ctrl+Click).
    - Flicker-free: redraws at 5Hz with fixed-width padding + erase-to-EOL.
    - set_activity() updates the tail (current tool / thinking / answering)."""

    REDRAW_INTERVAL = 0.2

    def __init__(self):
        self._start = None
        self._stop = threading.Event()
        self._thread = None
        self._round = 0
        self._activity = ""
        self._link_path = None  # launcher/work-log path for the hyperlink
        self._last_line_len = 0

    def start(self):
        self._start = time.time()
        self._round = 0
        self._activity = ""
        self._link_path = None
        self._last_line_len = 0
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def set_round(self, n):
        self._round = n

    def set_activity(self, text):
        self._activity = str(text)[:60]

    def set_link(self, path):
        self._link_path = str(path)

    def _render(self, frame):
        elapsed = int(time.time() - self._start)
        em = f"{elapsed // 60}m{elapsed % 60:02d}s" if elapsed >= 60 else f"{elapsed}s"
        _zh = _get_lang() == "zh"
        parts = [f"{frame} " + (_osc8_link(self._link_path, "工作中") if self._link_path else "工作中"),
                 em]
        if self._round:
            parts.append(f"{'第' + str(self._round) + '轮' if _zh else 'R' + str(self._round)}")
        if self._activity:
            parts.append(self._activity)
        return " · ".join(parts)

    def _run(self):
        frame_idx = 0
        last_draw = 0.0
        # ESC fallback poll (Windows): even if the global ESC listener thread
        # died, pressing ESC while "工作中" is showing stops the work at once.
        use_msvcrt = IS_WINDOWS
        if use_msvcrt:
            try:
                import msvcrt as _ms
            except Exception:
                use_msvcrt = False
        while not self._stop.is_set():
            if use_msvcrt:
                try:
                    while _ms.kbhit():
                        ch = _ms.getch()
                        if ch == b"\x1b":
                            _set_esc_stop()
                            _abort_active_responses()
                except Exception:
                    use_msvcrt = False
            now = time.time()
            if now - last_draw >= self.REDRAW_INTERVAL:
                line = self._render(SPIN_FRAMES[frame_idx % len(SPIN_FRAMES)])
                # \r + erase-to-EOL: no residue, no flicker from width changes
                sys.stderr.write("\r\x1b[K" + line)
                sys.stderr.flush()
                self._last_line_len = len(line)
                frame_idx += 1
                last_draw = now
            time.sleep(0.04)
        sys.stderr.write("\r\x1b[K")
        sys.stderr.flush()

    def stop(self):
        if self._thread is None:
            return
        self._stop.set()
        try:
            self._thread.join(timeout=1)
        except Exception:
            pass
        self._thread = None

    def elapsed_str(self):
        if not self._start:
            return "0s"
        e = int(time.time() - self._start)
        return f"{e // 60}m{e % 60:02d}s" if e >= 60 else f"{e}s"

# ════════════════════════════════════════════════════════════════
# §5  TUI — RICH CONSOLE & THEME
# ════════════════════════════════════════════════════════════════

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.text import Text
    from rich.syntax import Syntax
    from rich.table import Table
    from rich import box
    from rich.theme import Theme
except ImportError:
    print("Installing rich... ", end="", flush=True)
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "rich", "-q"])
    except Exception as _rich_install_err:
        print(f"\nFailed to install rich: {_rich_install_err}")
        print("Please install manually: pip install rich")
        sys.exit(1)
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.text import Text
    from rich.syntax import Syntax
    from rich.table import Table
    from rich import box
    from rich.theme import Theme

try:
    import psutil
except ImportError:
    psutil = None

try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

custom_theme = Theme({
    "info": "cyan",
    "warning": "yellow",
    "danger": "bold red",
    "success": "bold green",
    "tool": "bold blue",
    "result": "blue",
})
console = Console(theme=custom_theme)

# ── Clipboard Helper ──────────────────────────────────────────────

def _read_clipboard():
    """Read text from the system clipboard. Cross-platform (Windows/macOS/Linux)."""
    # Try pyperclip first (most reliable cross-platform)
    try:
        import pyperclip
        return pyperclip.paste()
    except (ImportError, Exception):
        pass

    # Platform-specific fallbacks
    if IS_WINDOWS:
        # PowerShell Get-Clipboard
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                capture_output=True, text=True, timeout=5,
                encoding='utf-8', errors='replace'
            )
            if result.returncode == 0 and result.stdout:
                return result.stdout
        except Exception:
            pass
        # Try win32clipboard as last resort on Windows
        try:
            import win32clipboard
            win32clipboard.OpenClipboard()
            try:
                data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                return data or ""
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            pass
    elif IS_MACOS:
        try:
            result = subprocess.run(
                ["pbpaste"],
                capture_output=True, text=True, timeout=5,
                encoding='utf-8', errors='replace'
            )
            if result.returncode == 0:
                return result.stdout
        except Exception:
            pass
    else:  # Linux
        # Try xclip
        try:
            result = subprocess.run(
                ["xclip", "-selection", "clipboard", "-o"],
                capture_output=True, text=True, timeout=5,
                encoding='utf-8', errors='replace'
            )
            if result.returncode == 0:
                return result.stdout
        except Exception:
            pass
        # Try xsel
        try:
            result = subprocess.run(
                ["xsel", "--clipboard", "--output"],
                capture_output=True, text=True, timeout=5,
                encoding='utf-8', errors='replace'
            )
            if result.returncode == 0:
                return result.stdout
        except Exception:
            pass
        # Try wl-paste (Wayland)
        try:
            result = subprocess.run(
                ["wl-paste"],
                capture_output=True, text=True, timeout=5,
                encoding='utf-8', errors='replace'
            )
            if result.returncode == 0:
                return result.stdout
        except Exception:
            pass

    return None  # Could not read clipboard

# ════════════════════════════════════════════════════════════════
# §6  RATE LIMITER & ESC STOP
# ════════════════════════════════════════════════════════════════

class RateLimiter:
    """Enforce minimum interval between API calls."""
    def __init__(self, min_interval=1.5):
        self.min_interval = min_interval
        self.last_call_time = 0.0
        self._lock = threading.Lock()

    def wait(self, silent=False, log_path=None):
        """Wait if needed to enforce rate limit.
        Args:
            silent: If True, suppress console output (for sub-agents).
            log_path: If provided, write wait message to this log file instead of console.
        """
        with self._lock:
            now = time.time()
            elapsed = now - self.last_call_time
            need_wait = self.min_interval - elapsed if elapsed < self.min_interval else 0
            # Always reserve the slot inside the lock (even when need_wait == 0),
            # so concurrent callers queue behind this reservation instead of all
            # waking up together after the sleep (thundering-herd bug).
            self.last_call_time = now + need_wait
        if need_wait > 0:
            wait_time = need_wait
            msg = f"⏳ {t('tool_rate_wait', time=f'{wait_time:.1f}')}"
            if log_path:
                try:
                    with open(log_path, "a", encoding="utf-8") as logf:
                        logf.write(f"{msg}\n")
                        logf.flush()
                except Exception:
                    pass
            elif not silent:
                if not _quiet_log_msg(msg):
                    console.print(f"[dim]{msg}[/dim]", end=" ", highlight=False)
            time.sleep(wait_time)

rate_limiter = RateLimiter(min_interval=1.5)

# --- Model Fallback State ---
class ModelFallbackState:
    """Tracks model fallback state: which model is active, when to switch back."""
    def __init__(self):
        self._lock = threading.Lock()
        # For main model
        self._main_using_backup = False
        self._main_backup_start_time = 0.0
        self._main_consecutive_errors = 0
        # For multimodal model
        self._mm_using_backup = False
        self._mm_backup_start_time = 0.0
        self._mm_consecutive_errors = 0
        # Backup cooldown: 60 seconds before trying main model again
        self.BACKUP_COOLDOWN = 60.0
        # Max consecutive non-429 errors before switching to backup
        self.MAX_CONSECUTIVE_ERRORS = 5
        # Max 429 retries before switching to backup (instead of immediate switch)
        self.MAX_429_RETRIES = 10

    def should_use_backup_main(self, config):
        """Check if we should use backup main model instead of primary.
        Returns (should_use_backup, active_model_name)."""
        with self._lock:
            primary = config.get("model", "glm-4-flash")
            backup = config.get("backupModel", "")
            if not backup:
                return False, primary
            if self._main_using_backup:
                # Check if 60s have passed since we started using backup
                if time.time() - self._main_backup_start_time >= self.BACKUP_COOLDOWN:
                    # Try switching back to primary
                    self._main_using_backup = False
                    self._main_consecutive_errors = 0
                    return False, primary
                return True, backup
            return False, primary

    def report_main_error(self, config, is_429=False):
        """Report an error from the main model. Returns (should_switch, active_model).
        For 429 errors: retry up to MAX_429_RETRIES times before switching to backup.
        For non-429 errors: retry up to MAX_CONSECUTIVE_ERRORS times before switching."""
        with self._lock:
            primary = config.get("model", "glm-4-flash")
            backup = config.get("backupModel", "")
            if is_429:
                # 429 → increment counter, switch after MAX_429_RETRIES
                self._main_consecutive_errors += 1
                if self._main_consecutive_errors >= self.MAX_429_RETRIES and backup:
                    self._main_using_backup = True
                    self._main_backup_start_time = time.time()
                    return True, backup
                return False, primary
            else:
                # Non-429: increment counter, switch after MAX_CONSECUTIVE_ERRORS
                self._main_consecutive_errors += 1
                if self._main_consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS and backup:
                    self._main_using_backup = True
                    self._main_backup_start_time = time.time()
                    return True, backup
                return False, primary

    def report_main_success(self):
        """Report a successful call from the main model."""
        with self._lock:
            self._main_consecutive_errors = 0

    def force_use_backup_main(self, config):
        """Force the main model into backup state, bypassing error counters.
        Used when primary retries are exhausted and we switch to backup directly.
        Without this, _main_using_backup stays False and the next round flips
        back to the primary model (primary<->backup flip-flop). Returns the
        backup model name ('' if none configured)."""
        with self._lock:
            backup = config.get("backupModel", "")
            if backup:
                self._main_using_backup = True
                self._main_backup_start_time = time.time()
                self._main_consecutive_errors = 0
            return backup

    def should_use_backup_mm(self, config):
        """Check if we should use backup multimodal model."""
        with self._lock:
            primary = config.get("multimodalModel", "")
            backup = config.get("backupMultimodalModel", "")
            if not primary or not backup:
                return False, primary
            if self._mm_using_backup:
                if time.time() - self._mm_backup_start_time >= self.BACKUP_COOLDOWN:
                    self._mm_using_backup = False
                    self._mm_consecutive_errors = 0
                    return False, primary
                return True, backup
            return False, primary

    def report_mm_error(self, config, is_429=False):
        """Report an error from the multimodal model."""
        with self._lock:
            primary = config.get("multimodalModel", "")
            backup = config.get("backupMultimodalModel", "")
            if is_429:
                if backup:
                    self._mm_using_backup = True
                    self._mm_backup_start_time = time.time()
                    return True, backup
                return False, primary
            else:
                self._mm_consecutive_errors += 1
                if self._mm_consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS and backup:
                    self._mm_using_backup = True
                    self._mm_backup_start_time = time.time()
                    return True, backup
                return False, primary

    def report_mm_success(self):
        """Report a successful call from the multimodal model."""
        with self._lock:
            self._mm_consecutive_errors = 0

model_fallback = ModelFallbackState()

# --- ESC Stop Mechanism ---
_esc_stop_flag = False
# Single-round thinking hard limit flag (30000 tokens) — separate from ESC
_thinking_force_stop_flag = False

# Open HTTP responses registered here get their socket closed the instant ESC
# is pressed, so a blocking SSE iterator aborts immediately instead of waiting
# until the model finishes speaking.
_active_http_lock = threading.Lock()
_active_http_responses = []

def _register_response(resp):
    with _active_http_lock:
        _active_http_responses.append(resp)

def _unregister_response(resp):
    with _active_http_lock:
        try:
            _active_http_responses.remove(resp)
        except ValueError:
            pass

def _abort_active_responses():
    with _active_http_lock:
        for r in list(_active_http_responses):
            try:
                r.close()
            except Exception:
                pass
        _active_http_responses.clear()

def _check_esc_stop():
    return _esc_stop_flag

def _set_esc_stop():
    global _esc_stop_flag
    _esc_stop_flag = True

def _reset_esc_stop():
    global _esc_stop_flag
    _esc_stop_flag = False

def _check_thinking_force_stop():
    return _thinking_force_stop_flag

def _reset_thinking_force_stop():
    global _thinking_force_stop_flag
    _thinking_force_stop_flag = False

def _trigger_thinking_force_stop():
    """Called from the SSE loop when one round's thinking exceeds the hard limit."""
    global _thinking_force_stop_flag
    _thinking_force_stop_flag = True
    _abort_active_responses()

_esc_listener_lock = threading.Lock()
_esc_listener_thread = None

def _esc_key_listener():
    """Background thread: listen for ESC key press.
    Uses a lock to prevent concurrent threads from corrupting terminal settings.
    On Linux/macOS, ensures terminal is always restored even on unexpected exit."""
    global _esc_stop_flag
    try:
        if IS_WINDOWS:
            import msvcrt
            while not _esc_stop_flag:
                if msvcrt.kbhit():
                    ch = msvcrt.getch()
                    if ch == b'\x1b':
                        _esc_stop_flag = True
                        _abort_active_responses()  # kill the in-flight SSE socket NOW
                        break
                time.sleep(0.05)
        else:
            import select
            import tty
            import termios
            # Only one listener thread at a time to avoid terminal settings race
            if not _esc_listener_lock.acquire(blocking=False):
                return  # Another listener is already active
            old_settings = None
            try:
                old_settings = termios.tcgetattr(sys.stdin)
                tty.setcbreak(sys.stdin.fileno())
                while not _esc_stop_flag:
                    readable, _, _ = select.select([sys.stdin], [], [], 0.1)
                    if readable:
                        ch = sys.stdin.read(1)
                        # Consume all buffered chars to avoid leaking into next input
                        while readable and not _esc_stop_flag:
                            if ch == '\x1b':
                                _esc_stop_flag = True
                                _abort_active_responses()  # kill the in-flight SSE socket NOW
                                # Drain any remaining escape sequence bytes
                                time.sleep(0.01)
                                drain_read, _, _ = select.select([sys.stdin], [], [], 0.02)
                                while drain_read:
                                    sys.stdin.read(1)
                                    drain_read, _, _ = select.select([sys.stdin], [], [], 0.01)
                                break
                            # Non-ESC char during thinking: discard (prevents ghost input)
                            drain_read, _, _ = select.select([sys.stdin], [], [], 0.01)
                            if drain_read:
                                ch = sys.stdin.read(1)
                            else:
                                break
            except Exception:
                pass
            finally:
                # ALWAYS restore terminal settings before exiting
                if old_settings is not None:
                    try:
                        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
                    except Exception:
                        pass
                _esc_listener_lock.release()
    except Exception:
        pass

def _start_esc_listener():
    global _esc_listener_thread
    _reset_esc_stop()
    # One listener at a time: a previous turn's thread may still be alive
    # (it loops while the flag is False), so reuse it instead of stacking.
    if _esc_listener_thread is not None and _esc_listener_thread.is_alive():
        return _esc_listener_thread
    _esc_listener_thread = threading.Thread(target=_esc_key_listener, daemon=True)
    _esc_listener_thread.start()
    return _esc_listener_thread

# --- Open in New Terminal ---
# Thinking & sub-agent log directories
THINKING_LOG_DIR = ZAGENT_HOME / "thinking"
THINKING_LOG_DIR.mkdir(parents=True, exist_ok=True)

_current_thinking_log = None  # Path to current thinking log file

# --- Embedded Rich UI Viewer Script ---
# This Python script is written to ~/.zagent/bin/zagent_viewer.py and used
# to render log files with Markdown, Panels, and syntax highlighting
# when opening them in a new terminal window.
_VIEWER_SCRIPT = r'''#!/usr/bin/env python3
"""ZAgent Log Viewer - Renders thinking/sub-agent logs with Rich UI
Matches the main terminal's rendering style exactly."""
import sys, time, re, json
from pathlib import Path

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.text import Text
    from rich.theme import Theme
    from rich.syntax import Syntax
    from rich import box
except ImportError:
    p = sys.argv[1] if len(sys.argv) > 1 else ""
    if p:
        import subprocess
        if sys.platform == "win32":
            subprocess.run(["powershell", "-NoExit", "-Command",
                            "Get-Content -LiteralPath '{}' -Wait -Encoding UTF8".format(p)])
        else:
            subprocess.run(["tail", "-f", p])
    sys.exit(0)

console = Console(theme=Theme({
    "info": "cyan", "warning": "yellow", "tool": "bold blue",
    "result": "blue", "success": "bold green", "danger": "bold red",
}))

APP_NAME = "ZAgent"

def _render(section_type, content, title=None):
    content = content.strip()
    if not content:
        return
    try:
        if section_type == "assistant":
            console.print(Panel(Markdown(content), title=title or f"🤖 {APP_NAME}",
                                border_style="green", padding=(1, 2), box=box.ROUNDED))
        elif section_type == "tool_call":
            # Match main terminal: inline styled text, NOT a Panel
            console.print(f"\n[tool]🔧 Calling tool: {title}({content})[/tool]")
        elif section_type == "tool_result":
            console.print(Panel(Markdown(content), title=title or "📋 Result",
                                border_style="blue", padding=(1, 2), box=box.ROUNDED))
        elif section_type == "thinking":
            console.print(Panel(Markdown(content), title=title or "💭 Thinking",
                                border_style="yellow", padding=(1, 2), box=box.ROUNDED))
        elif section_type == "error":
            console.print(Panel(content, title=title or "Error",
                                border_style="red", padding=(0, 2), box=box.ROUNDED))
        else:
            console.print(content)
    except Exception:
        console.print(Panel(content, title=title, border_style="blue", padding=(1, 2)))

def view_log(log_path, follow=True):
    log_path = Path(log_path)
    _waited = False
    for _ in range(30):
        if log_path.exists():
            break
        if not _waited:
            console.print("[dim]⏳ 等待日志写入... (Waiting for log content...)[/dim]")
            _waited = True
        time.sleep(0.5)
    else:
        console.print(f"[red]Log not found: {log_path}[/red]")
        input("Press Enter to close...")
        return

    pos = 0
    cur = None
    cur_title = None
    lines = []

    def flush():
        nonlocal cur, cur_title, lines
        if cur and lines:
            _render(cur, "\n".join(lines), cur_title)
        lines = []
        cur = None
        cur_title = None

    while True:
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(pos)
                new = f.readlines()
                pos = f.tell()
        except Exception:
            time.sleep(0.5)
            continue

        for line in new:
            line = line.rstrip("\n").rstrip("\r")

            # --- Sub-agent log sections ---
            if line.startswith("[Assistant round"):
                flush()
                m = re.match(r"\[Assistant round (\d+)\]:", line)
                rnd = m.group(1) if m else "?"
                cur = "assistant"
                cur_title = f"🤖 {APP_NAME} (round {rnd})"

            elif line.startswith("[Tool:"):
                flush()
                m = re.match(r"\[Tool: (\w+)\((.*)\)\]:", line)
                if m:
                    tname, targs = m.group(1), m.group(2)
                    # Display as inline styled text (matching main terminal)
                    args_display = targs
                    if len(args_display) > 80:
                        args_display = args_display[:77] + "..."
                    console.print(f"\n[tool]🔧 调用工具: {tname}({args_display})[/tool]")
                else:
                    console.print(f"\n[tool]🔧 Tool Call: {line[7:]}[/tool]")
                cur = None

            elif line.startswith("[Tool Result:"):
                flush()
                m = re.match(r"\[Tool Result: (\w+)\]:", line)
                tname = m.group(1) if m else "Result"
                cur = "tool_result"
                cur_title = f"📋 结果: {tname}"

            # --- Thinking log sections ---
            elif line.startswith("[Thinking]"):
                flush()
                cur = "thinking"
                cur_title = "💭 思考过程"

            # --- Common markers ---
            elif line.startswith("====="):
                # Only flush if not in thinking mode (==== is a separator in thinking logs)
                if cur != "thinking":
                    flush()
                # Skip the separator line itself

            elif line.startswith("Sub-agent:"):
                console.print(f"[bold cyan]{line}[/bold cyan]")

            elif line.startswith("Started:") or line.startswith("Prompt:"):
                console.print(f"[dim]  {line}[/dim]")

            elif line.startswith("Completed:"):
                flush()
                console.print(f"[bold green]✅ {line}[/bold green]")

            elif line.startswith("Result:"):
                flush()
                r = line[7:].strip()
                if r:
                    _render("assistant", r, f"🤖 {APP_NAME}")

            elif line.startswith("ERROR:"):
                flush()
                _render("error", line[6:], "Error")

            elif line.startswith("💭 思考过程"):
                flush()
                cur = "thinking"
                cur_title = "💭 思考过程"
                # Don't print the header yet - it will be the Panel title

            elif line.startswith("思考完成"):
                flush()
                console.print(f"[dim]{line}[/dim]")

            else:
                if cur == "thinking" and follow:
                    # Live-stream thinking lines as they arrive (no buffering) —
                    # the viewer shows thinking in real time while it generates.
                    if line.strip():
                        console.print(Text(line, style="dim italic"))
                elif cur in ("assistant", "tool_result", "thinking"):
                    lines.append(line)
                elif line.strip():
                    console.print(line)

        if not follow:
            flush()
            break
        time.sleep(0.3)

    flush()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python zagent_viewer.py <log_path>")
        sys.exit(1)
    try:
        view_log(sys.argv[1], follow=True)
    except KeyboardInterrupt:
        console.print("\n[dim]Viewer closed.[/dim]")
'''

def _ensure_viewer_script():
    """Write the embedded Rich UI viewer script to ~/.zagent/bin/ if needed.
    Returns the path to the viewer script."""
    viewer_dir = ZAGENT_HOME / "bin"
    viewer_dir.mkdir(parents=True, exist_ok=True)
    viewer_path = viewer_dir / "zagent_viewer.py"
    # Always overwrite to keep in sync with latest version
    with open(viewer_path, "w", encoding="utf-8") as f:
        f.write(_VIEWER_SCRIPT)
    return viewer_path

def _create_log_launcher(log_path, title="ZAgent Log"):
    """Create a launcher script that opens a new terminal with the Rich UI viewer.
    The viewer renders logs with Markdown, Panels, and syntax highlighting.
    Returns the launcher script path."""
    log_path = Path(log_path)
    viewer_path = _ensure_viewer_script()
    # Sanitize title (remove emoji for bat/cmd compatibility)
    safe_title = re.sub(r'[^\x20-\x7E\u4e00-\u9fff]', '', title).strip() or "ZAgent Log"

    if IS_WINDOWS:
        launcher_path = log_path.with_suffix('.bat')
        viewer_str = str(viewer_path).replace('/', '\\')
        log_str = str(log_path).replace('/', '\\')
        # Use sys.executable to get the correct Python path
        python_exe = sys.executable.replace('/', '\\')
        bat_content = (
            "@echo off\n"
            "chcp 65001 >nul 2>&1\n"
            f'title {safe_title}\n'
            f'"{python_exe}" "{viewer_str}" "{log_str}"\n'
            "pause\n"
        )
        with open(launcher_path, 'w', encoding='utf-8', errors='replace') as f:
            f.write(bat_content)
    else:
        launcher_path = log_path.with_suffix('.sh')
        viewer_str = str(viewer_path)
        log_str = str(log_path)
        content = f'''#!/bin/bash
gnome-terminal --title "{safe_title}" -- {sys.executable} "{viewer_str}" "{log_str}" 2>/dev/null || \\
x-terminal-emulator -e "{sys.executable} {viewer_str} {log_str}" 2>/dev/null || \\
xterm -e "{sys.executable} {viewer_str} {log_str}" 2>/dev/null || \\
konsole -e "{sys.executable} {viewer_str} {log_str}" 2>/dev/null
'''
        with open(launcher_path, 'w', encoding='utf-8') as f:
            f.write(content)
        os.chmod(launcher_path, 0o755)
    return launcher_path

def _make_clickable_link(file_path, label="点击查看"):
    """Create a Rich markup string with an OSC 8 hyperlink.
    In Windows Terminal, Ctrl+Click opens the file with the default application.
    For .bat launcher files, this will execute the launcher and open a new terminal."""
    try:
        file_path = Path(file_path)
        uri = file_path.as_uri()
        return f"[link={uri}]{label}[/link]"
    except Exception:
        return label

def _open_in_new_terminal(file_path, title="ZAgent Log"):
    """Open a file in a new terminal window for real-time viewing.
    Uses the launcher script created by _create_log_launcher."""
    launcher_path = _create_log_launcher(file_path, title=title)
    try:
        if IS_WINDOWS:
            # Use os.startfile for reliable execution on Windows
            os.startfile(str(launcher_path))
        else:
            subprocess.Popen([str(launcher_path)], start_new_session=True)
        return True
    except Exception as e:
        # Fallback: try os.system
        try:
            if IS_WINDOWS:
                os.system(f'start "" "{str(launcher_path)}"')
            else:
                os.system(f'bash "{str(launcher_path)}" &')
            return True
        except Exception as e2:
            console.print(f"[dim]Failed to open terminal: {e2}[/dim]")
            return False

# ════════════════════════════════════════════════════════════════
# §7  TOOL DEFINITIONS (OpenAI function calling format)
# ════════════════════════════════════════════════════════════════

TOOL_DEFINITIONS = [
    # --- 6 Coding-Essential Tools ---
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a shell command with cross-platform auto-adaptation (PowerShell/cmd on Windows, bash on Linux/macOS). Returns stdout, stderr, and exit code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default 30, max 300)", "default": 30}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read file content with line numbers, or list directory contents if path is a directory. Auto-detects encoding. Supports offset and limit for large files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File or directory path"},
                    "offset": {"type": "integer", "description": "Starting line number (1-based, default 1)", "default": 1},
                    "limit": {"type": "integer", "description": "Max lines to read (default 2000)", "default": 2000}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": "Write content to a file. Creates parent directories if needed. Auto-normalizes line endings for the current platform.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "content": {"type": "string", "description": "File content to write"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit",
            "description": "Precisely edit a file using one of 5 matching strategies: 'exact' (byte-for-byte match), 'line' (line number range), 'prefix_suffix' (match by prefix+suffix), 'regex' (regex pattern), 'fuzzy' (whitespace-tolerant match). Replaces matched content with new content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "old_string": {"type": "string", "description": "Content to find (for exact/prefix_suffix/fuzzy strategies)"},
                    "new_string": {"type": "string", "description": "Replacement content"},
                    "strategy": {"type": "string", "description": "Matching strategy: 'exact' (default), 'line', 'prefix_suffix', 'regex', 'fuzzy'", "default": "exact", "enum": ["exact", "line", "prefix_suffix", "regex", "fuzzy"]},
                    "start_line": {"type": "integer", "description": "Start line for 'line' strategy (1-based)"},
                    "end_line": {"type": "integer", "description": "End line for 'line' strategy (inclusive)"},
                    "prefix": {"type": "string", "description": "Prefix for 'prefix_suffix' strategy"},
                    "suffix": {"type": "string", "description": "Suffix for 'prefix_suffix' strategy"},
                    "pattern": {"type": "string", "description": "Regex pattern for 'regex' strategy"},
                    "replace_all": {"type": "boolean", "description": "Replace all occurrences (default false, only for exact/regex)", "default": False}
                },
                "required": ["path", "new_string"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files matching a glob pattern. Supports ** for recursive search.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern (e.g. '**/*.py', 'src/**/*.ts')"},
                    "path": {"type": "string", "description": "Base directory to search (default: current dir)", "default": "."}
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search for text/regex patterns in files. Returns matching lines with file paths and line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Search pattern (regex or literal text)"},
                    "path": {"type": "string", "description": "Directory or file path to search", "default": "."},
                    "include": {"type": "string", "description": "File glob filter (e.g. '*.py', '*.{ts,tsx}')", "default": "*"},
                    "literal": {"type": "boolean", "description": "Treat pattern as literal text (escape regex chars)", "default": False},
                    "case_insensitive": {"type": "boolean", "description": "Case insensitive search", "default": False},
                    "max_results": {"type": "integer", "description": "Max results to return (default 100)", "default": 100}
                },
                "required": ["pattern"]
            }
        }
    },
    # --- Utility Tools ---
    {
        "type": "function",
        "function": {
            "name": "mkdir",
            "description": "Create directory (and parent directories)",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Directory path"}},
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "rm",
            "description": "Remove file or directory",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Path to remove"}},
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git",
            "description": "Run git command",
            "parameters": {
                "type": "object",
                "properties": {"args": {"type": "string", "description": "Git arguments"}},
                "required": ["args"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "sysinfo",
            "description": "Show system information",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tree",
            "description": "Show directory tree structure",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "."},
                    "depth": {"type": "integer", "default": 3}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cd",
            "description": "Change current working directory",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Target directory"}},
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "pwd",
            "description": "Show current working directory",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for information. Returns search results with titles, URLs, and snippets. Use this when you need current information, facts, or data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "num_results": {"type": "integer", "description": "Number of results to return (default 5, max 10)", "default": 5}
                },
                "required": ["query"]
            }
        }
    },
    # --- Sub-Agent Tool ---
    {
        "type": "function",
        "function": {
            "name": "task",
            "description": "Spawn a sub-agent to handle a sub-task. The sub-agent runs independently with its own context. Use 'resume' with a previous sub-agent ID to continue that sub-agent's conversation. The 'model' parameter is REQUIRED — you must specify the name of a configured sub-agent model (e.g., the name you gave when configuring sub-agent models like 'imager', 'videoer', 'writer', etc.). Do NOT use raw model IDs or numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "Short 3-5 word description of the sub-task"},
                    "prompt": {"type": "string", "description": "Detailed instructions for the sub-agent. When resuming, this is the follow-up instruction to continue."},
                    "resume": {"type": "integer", "description": "Previous sub-agent ID to resume (continue its conversation with full context). If omitted, starts a new sub-agent."},
                    "model": {"type": "string", "description": "REQUIRED: Name of a configured sub-agent model (must match the name you configured in subagentModels, e.g., 'imager', 'videoer', 'writer'). Do NOT use raw model IDs or numbers."}
                },
                "required": ["description", "prompt", "model"]
            }
        }
    },
    # --- AI Media Tools ---
    {
        "type": "function",
        "function": {
            "name": "image_view",
            "description": "Analyze/understand an image. If the main model supports vision, uses it directly. Otherwise, uses the configured multimodal model. Supports local file paths and URLs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "image": {"type": "string", "description": "Image path (local file) or URL to analyze"},
                    "question": {"type": "string", "description": "What to ask about the image (default: describe what you see)", "default": "Describe what you see in this image in detail."}
                },
                "required": ["image"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "video_view",
            "description": "Analyze/understand a video. Uses a multimodal model that supports video understanding (e.g., glm-4v-flash, glm-4.1v-thinking-flash). Supports video URLs and local file paths. The model will describe and analyze the video content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "video": {"type": "string", "description": "Video path (local file) or URL to analyze"},
                    "question": {"type": "string", "description": "What to ask about the video (default: describe what you see)", "default": "Describe what happens in this video in detail."}
                },
                "required": ["video"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "image_generate",
            "description": "Generate an AI image from a text prompt. Uses CogView-3-Flash (free) or configured image generation model.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Image generation prompt (detailed description of desired image)"},
                    "size": {"type": "string", "description": "Image size: '1024x1024' (default), '768x1344', '864x1152', '1344x768', '1152x864', '1440x720', '720x1440'", "default": "1024x1024"},
                    "output": {"type": "string", "description": "Output file path (default: auto-generated in current directory)", "default": ""}
                },
                "required": ["prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "video_generate",
            "description": "Generate an AI video from a text prompt. Uses CogVideoX-Flash (free) or configured video generation model. Video generation is asynchronous - may take several minutes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Video generation prompt (detailed description of desired video)"},
                    "output": {"type": "string", "description": "Output file path (default: auto-generated in current directory)", "default": ""}
                },
                "required": ["prompt"]
            }
        }
    },
]

SYSTEM_PROMPT_DEFAULT = f"""You are ZAgent, a powerful AI coding assistant running in the terminal.
You help users with coding, file operations, system tasks, and more.
Use the provided tools to help the user. Always respond in the user's language.

## Environment
- OS: {platform.system()} {platform.release()} ({platform.machine()})
- Shell: {SHELL_NAME}
- Working Directory: {os.getcwd()}

## Sub-Agent Strategy
For complex, multi-step, or time-consuming tasks, prefer using the `task` tool to delegate to sub-agents.
Sub-agents can work in parallel and keep the main conversation focused.
Examples of good sub-agent tasks: researching a topic, analyzing multiple files, implementing a feature, running tests.
Simple or quick tasks should be handled directly.
If a sub-agent's work is incomplete or needs follow-up, use the `resume` parameter with its ID to continue the conversation.
This preserves the sub-agent's full context so it can pick up where it left off.

## Multi-Model Strategy
You have access to multiple AI models with different capabilities:
- **Main Model**: Your primary model for text tasks. Some main models also support vision (image understanding).
- **Multimodal Model**: A dedicated vision model used when the main model doesn't support images/videos.
- **Sub-Agent Models**: You MUST specify a model name from configured subagentModels when using the `task` tool. The model parameter is REQUIRED — you must use one of the configured sub-agent model names.

### When to Use Each Model:
- If the user asks you to analyze/view an image → use the `image_view` tool (auto-selects the right model)
- If the user asks you to analyze/view a video → use the `video_view` tool (auto-selects a video-capable multimodal model)
- If the user wants to generate an image → use the `task` tool with model set to the configured image-gen sub-agent name, and prompt describing the desired image
- If the user wants to generate a video → use the `task` tool with model set to the configured video-gen sub-agent name, and prompt describing the desired video
- You CANNOT generate images or videos yourself — use the `task` tool to delegate to a sub-agent with the appropriate model
- The `model` parameter in the `task` tool is REQUIRED. You must use the exact name from the configured Sub-Agent Models listed below.

### Available Model Types:
- **Text models** (e.g., glm-4-flash, deepseek-chat): General conversation and coding
- **Vision models** (e.g., glm-4v-flash): Image understanding, screenshot analysis
- **Video models** (e.g., glm-4v-flash, glm-4.1v-thinking-flash): Video understanding and analysis
- **Reasoning models** (e.g., glm-z1-flash, deepseek-reasoner): Complex logical reasoning, math, code debugging
- **Image generation models** (e.g., cogview-3-flash): Create images from text descriptions
- **Video generation models** (e.g., cogvideox-flash): Create videos from text descriptions

IMPORTANT: You CANNOT directly generate images or videos — these tools are sub-agent ONLY. When the user wants an image or video, you MUST use the `task` tool with the appropriate model parameter. The model parameter MUST be one of the configured Sub-Agent Model names listed in the model configuration section above. Do NOT use model IDs like 'cogview-3-flash' or numbers like '6'/'7' — use the EXACT configured name.

## Tool Usage Guidelines
- Use `bash` for shell commands (auto-adapts to PowerShell/cmd on Windows, bash on Linux)
- Use `read` to view file contents (with line numbers, auto-encoding)
- Use `write` to create/overwrite files
- Use `edit` for precise code edits (5 strategies: exact, line, prefix_suffix, regex, fuzzy)
- Use `glob` to find files by pattern
- Use `grep` to search file contents
- Use `web_search` for current information from the internet
- Use `task` to delegate complex sub-tasks to sub-agents (MUST specify model name from configured sub-agent models)
- Use `image_view` to analyze/understand images (auto-selects multimodal model if needed)
- Use `video_view` to analyze/understand videos (auto-selects video-capable multimodal model if needed)
- Use `task` to delegate image/video generation to sub-agents (you cannot generate images/videos yourself)
- Sub-agents with image_gen capability can use `image_generate`, and sub-agents with video_gen capability can use `video_generate`

## Edit Tool Strategies
- `exact`: Match old_string byte-for-byte (most precise, default)
- `line`: Replace lines by line number range (start_line to end_line)
- `prefix_suffix`: Match content between prefix and suffix
- `regex`: Match using regex pattern, replace with new_string
- `fuzzy`: Whitespace-tolerant match (trims whitespace for comparison)

IMPORTANT: When you want to use a tool, you MUST use the native function calling mechanism (tool_calls), NOT text formatting.
Do NOT output tool calls as text like "TOOL_CALL: ..." - instead, use the proper function calling API."""

# ════════════════════════════════════════════════════════════════
# §8  TOOL EXECUTION ENGINE
# ════════════════════════════════════════════════════════════════

# ── §8a Token Estimation & Context Compression ─────────────────

def _estimate_tokens(text):
    """Rough token estimation: ~0.75 tokens per English char, ~0.5 per Chinese char."""
    if not text:
        return 0
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other_chars = len(text) - chinese_chars
    return int(chinese_chars * 2 + other_chars * 0.33)

def _estimate_messages_tokens(messages):
    """Estimate total tokens for a list of messages."""
    total = 0
    for msg in messages:
        if isinstance(msg, dict):
            content = msg.get("content", "")
            if content:
                total += _estimate_tokens(str(content))
            # Count role overhead
            total += 4  # approximate overhead per message
    return total

def _count_rounds(messages):
    """Count conversation rounds (user messages = 1 round each)."""
    return sum(1 for m in messages if isinstance(m, dict) and m.get("role") == "user")

# Recent-context budget kept fully intact during compression (tokens).
KEEP_RECENT_TOKENS = 32000

def _recent_boundary_idx(messages, keep_tokens=KEEP_RECENT_TOKENS):
    """Find the index where the 'recent, keep intact' window starts.
    Walk backwards accumulating estimated tokens until keep_tokens is reached."""
    if keep_tokens is None or keep_tokens <= 0:
        return 0
    acc = 0
    boundary_idx = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        t_budget = 4
        if isinstance(msg, dict) and msg.get("content"):
            t_budget += _estimate_tokens(str(msg.get("content")))
        if acc + t_budget > keep_tokens and boundary_idx < len(messages):
            break
        acc += t_budget
        boundary_idx = i
    return boundary_idx

def _strip_old_tool_calls(messages, keep_tokens=KEEP_RECENT_TOKENS):
    """For messages older than the recent token budget, strip tool call details.
    Keeps user/assistant text but removes tool/tool_calls payloads.
    The recent window (sized by tokens, not rounds) is kept completely intact.

    IMPORTANT: When removing tool result messages, we must also remove
    tool_calls from the corresponding assistant messages. Otherwise the API
    receives an assistant message with tool_calls but no matching tool result,
    which causes a 400 error.
    """
    if len(messages) <= 4:  # Too few to bother
        return messages

    # Token-based boundary: everything at/after boundary_idx stays intact
    boundary_idx = _recent_boundary_idx(messages, keep_tokens)
    if boundary_idx <= 0:
        return messages  # Everything fits in the keep window

    # First pass: identify which assistant messages in the old region have tool_calls
    # so we know they need to be cleaned up together with their tool results
    old_assistant_tool_call_ids = set()
    for i, msg in enumerate(messages):
        if i >= boundary_idx:
            break
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "assistant":
            for tc in (msg.get("tool_calls") or msg.get("_raw_api_msg", {}).get("tool_calls", [])):
                tc_id = tc.get("id", "")
                if tc_id:
                    old_assistant_tool_call_ids.add(tc_id)

    result = []
    for i, msg in enumerate(messages):
        if i >= boundary_idx:
            # Within keep window: keep everything intact
            result.append(msg)
        else:
            # Old message: strip tool call details
            role = msg.get("role", "") if isinstance(msg, dict) else ""
            if role == "tool":
                # Skip tool result messages entirely for old rounds
                # (their corresponding assistant tool_calls will also be removed)
                continue
            elif role == "assistant":
                # Keep assistant text but remove tool_calls metadata
                raw_msg = msg.get("_raw_api_msg", msg)
                has_tool_calls = bool(msg.get("tool_calls") or raw_msg.get("tool_calls"))
                clean = {"role": "assistant", "content": msg.get("content", "")}
                # If there were tool calls, add a brief summary note
                if has_tool_calls:
                    tc_names = []
                    for tc in (msg.get("tool_calls") or raw_msg.get("tool_calls", [])):
                        tc_names.append(tc.get("function", {}).get("name", "?"))
                    if tc_names:
                        existing = clean["content"] or ""
                        clean["content"] = existing + f"  [called tools: {', '.join(tc_names)}]" if existing else f"[called tools: {', '.join(tc_names)}]"
                # Do NOT include tool_calls or _raw_api_msg — they would reference
                # tool results that we just deleted, causing API 400 errors
                result.append(clean)
            else:
                # user/system messages: keep as-is
                result.append(msg)

    return result

def compress_context(messages, system_prompt=None, force_llm_summary=False):
    """Compress conversation context with a two-stage, TOKEN-BASED strategy:
    Stage 1: Strip tool call details from messages outside the recent token budget.
    Stage 2 (if still over limit or forced): LLM-summarize the old messages.
    The recent KEEP_RECENT_TOKENS token window is ALWAYS preserved intact.
    All user-facing numbers are tokens (never round counts) since 26w35a.
    """
    threshold = get_context_compression_threshold()
    total_tokens = _estimate_messages_tokens(messages)

    if total_tokens <= threshold and not force_llm_summary:
        return messages  # Nothing to compress

    console.print(f"[dim]{t('context.compressing')} (~{total_tokens}/{threshold} tok)[/dim]")

    # Stage 1: Strip old tool calls (always, even for force_llm_summary to reduce token usage)
    stripped = _strip_old_tool_calls(messages, keep_tokens=KEEP_RECENT_TOKENS)
    stripped_tokens = _estimate_messages_tokens(stripped)

    # If stripping was enough and not forced, we're done
    if stripped_tokens <= threshold and not force_llm_summary:
        n_stripped = len(messages) - len(stripped)
        console.print(f"[success]{t('context.compressed_tok', tokens=stripped_tokens)} ({n_stripped} msgs stripped)[/success]")
        return stripped

    # Stage 2: LLM summary of old messages (boundary is token-based)
    boundary_idx = _recent_boundary_idx(stripped, KEEP_RECENT_TOKENS)
    if boundary_idx <= 0:
        boundary_idx = max(0, len(stripped) // 2)

    # Never start the kept window on a tool message: its assistant tool_calls
    # partner lives in the summarized zone, and the API rejects an orphan
    # tool result → extend the boundary until it starts on a normal message
    while boundary_idx < len(stripped) and stripped[boundary_idx].get("role") == "tool":
        boundary_idx += 1
    if boundary_idx >= len(stripped):
        return stripped  # Degenerate: everything is tool results, keep as-is

    old_messages = stripped[:boundary_idx]
    recent_messages = stripped[boundary_idx:]

    # Build a text representation of old messages for LLM summarization
    conv_text = ""
    for msg in old_messages:
        role = msg.get("role", "unknown") if isinstance(msg, dict) else "unknown"
        content = msg.get("content", "") if isinstance(msg, dict) else ""
        if content:
            conv_text += f"[{role}]: {content}\n\n"

    if not conv_text.strip():
        return stripped

    # Ask LLM to summarize, preserving key memory
    _lang = _get_lang()
    if _lang == "zh":
        summary_instruction = (
            "请仔细阅读以下对话历史，提取并保留所有重要的记忆信息。\n"
            "包括：关键决策、代码变更、文件路径、用户偏好、未完成的任务、重要的上下文信息。\n"
            "省略：工具调用的详细输出、重复信息、已完成的中间步骤。\n"
            "用简洁但完整的方式总结，确保后续对话不会丢失关键信息。"
        )
    else:
        summary_instruction = (
            "Please review the conversation history below and extract all important memory.\n"
            "Include: key decisions, code changes, file paths, user preferences, unfinished tasks, important context.\n"
            "Omit: detailed tool outputs, duplicate info, completed intermediate steps.\n"
            "Summarize concisely but completely, ensuring no critical information is lost for the ongoing conversation."
        )

    summary_prompt = (
        f"{summary_instruction}\n\n"
        f"--- Conversation to summarize ---\n{conv_text}\n--- End ---"
    )

    full_config = load_full_config()
    model = full_config.get("model", "glm-4-flash")
    summary = llm_summarize(
        "You are a helpful assistant that summarizes conversations concisely while preserving all important details for future reference.",
        summary_prompt, model=model, full_config=full_config, max_tokens=2000,
    )
    if not summary:
        return stripped  # Compression failed: keep the stripped version, never destroy history

    # Replace old messages with summary, keep recent token window intact
    compressed = [
        {"role": "system", "content": f"[Previous conversation summary]\n{summary}"},
    ] + recent_messages

    new_tokens = _estimate_messages_tokens(compressed)
    console.print(f"[success]{t('context.compressed_tok', tokens=new_tokens)}[/success]")
    return compressed


def llm_summarize(system_instruction, user_content, model=None, full_config=None, max_tokens=2000, timeout=120):
    """Non-streaming one-shot LLM summarization used by context compression and
    chain-of-thought compression. Routes through the multi-key endpoint resolver.
    Returns summary string, or None on failure (caller MUST NOT discard originals)."""
    if full_config is None:
        full_config = load_full_config()
    if not model:
        model = full_config.get("model", "glm-4-flash")
    base_url, api_key = resolve_model_endpoint(model, full_config)
    if not base_url or not api_key:
        return None
    try:
        import urllib.request
        url = f"{base_url}/chat/completions"
        body = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": max_tokens,
            "stream": False,
        }, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return (data["choices"][0]["message"].get("content") or "").strip() or None
    except Exception:
        return None

# ── §8b Thinking Mode ──────────────────────────────────────────

def get_thinking_mode():
    """Get current thinking mode from config. Returns 'on' or 'off'."""
    cfg = load_full_config()
    return cfg.get("thinkingMode", "off")

def set_thinking_mode(mode):
    """Set thinking mode in config. mode: 'on' or 'off'."""
    cfg = load_full_config()
    cfg["thinkingMode"] = mode
    save_config(cfg)

# Model context window sizes (approximate, in tokens)
MODEL_CONTEXT_WINDOWS = {
    "glm-4.7-flash": 128000,
    "glm-4-flash": 128000,
    "glm-4-plus": 128000,
    "glm-4-0520": 128000,
    "glm-4-air": 128000,
    "glm-4-long": 1000000,
    "glm-4v": 128000,
    "deepseek-chat": 64000,
    "deepseek-reasoner": 64000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4.1": 1000000,
    "gpt-4.1-mini": 1000000,
    "o3-mini": 200000,
    "qwen-turbo": 128000,
    "qwen-plus": 128000,
    "qwen-max": 32000,
    "qwq-32b": 128000,
    "doubao-1.5-pro": 128000,
    "doubao-1.5-lite": 128000,
    # Agnes AI
    "agnes-2.5-flash": 512000,
    "agnes-2.0-flash": 256000,
    "agnes-1.5-flash": 256000,
}

def get_context_compression_threshold():
    """Get context compression threshold based on the current model's context window.
    Triggers compression at 80% of the model's context window."""
    config = load_zai_config()
    if not config:
        return 102400  # Default: 80% of 128K
    model = config.get("model", "glm-4-flash")
    context_window = MODEL_CONTEXT_WINDOWS.get(model, 128000)
    return int(context_window * 0.80)

# ── §8c Sub-Agent System ──────────────────────────────────────

# Sub-agent log directory
SUBAGENT_LOG_DIR = ZAGENT_HOME / "subagents"
SUBAGENT_LOG_DIR.mkdir(parents=True, exist_ok=True)

# Active sub-agents tracking
_active_subagents = {}
_completed_subagents = {}  # subagent_id -> SubAgentRunner (kept for resume)
_COMPLETED_SUBAGENTS_MAX = 20  # Keep at most this many completed sub-agents for resume
_subagent_lock = threading.Lock()
_subagent_counter = 0

# Braille dot frames for spinning animation
SPIN_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

class SubAgentRunner:
    """Runs a sub-agent in a background thread."""

    def __init__(self, description, prompt, subagent_id, model=None):
        self.description = description
        self.prompt = prompt
        self.subagent_id = subagent_id
        self.model = model  # Sub-agent model override (None = use default)
        self.log_path = SUBAGENT_LOG_DIR / f"subagent_{subagent_id}.log"
        self.result = None
        self.error = None
        self.completed = False
        self.thread = None
        self._start_time = time.time()
        self.messages = []  # Conversation messages (for resume)
        self.system_prompt = ""  # System prompt used (for resume)
        self._is_resume = False  # Whether this is a resumed sub-agent
        self._resume_messages = None  # Previous messages to continue from

    def run(self):
        """Execute the sub-agent task in the current thread."""
        try:
            mode = "Resumed" if self._is_resume else "Started"
            with open(self.log_path, "a" if self._is_resume else "w", encoding="utf-8") as logf:
                logf.write(f"\n{'='*50}\nSub-agent: {self.description} ({mode})\n")
                logf.write(f"{mode}: {datetime.now().isoformat()}\n")
                if self.model:
                    logf.write(f"Model: {self.model}\n")
                logf.write(f"Prompt: {self.prompt}\n")
                logf.write("=" * 50 + "\n\n")

            # Build messages for the sub-agent
            system_prompt = get_system_prompt()
            ctx = load_context_instructions()
            if ctx:
                system_prompt = system_prompt + "\n\n## Project Context\n" + ctx
            system_prompt += "\n\nYou are a sub-agent. Complete your task and provide a clear result."
            system_prompt += "\n\nIMPORTANT: You CANNOT use the `task` tool (sub-agents cannot spawn further sub-agents). Use other available tools directly."
            # If using a specific model, inform the sub-agent about its capabilities
            if self.model:
                caps = MODEL_CAPABILITIES.get(self.model, set())
                if caps:
                    cap_str = ", ".join(sorted(caps))
                    system_prompt += f"\n\nYou are running on model: {self.model} (capabilities: {cap_str})."
                    # Explicitly inform the sub-agent about available tools based on model capabilities
                    if "image_gen" in caps:
                        system_prompt += "\n\n🎨 You have the `image_generate` tool available. When asked to create/generate an image, call the `image_generate` tool with a `prompt` parameter describing the desired image. Example: image_generate(prompt=\"a beautiful sunset over mountains\")"
                    if "video_gen" in caps:
                        system_prompt += "\n\n🎬 You have the `video_generate` tool available. When asked to create/generate a video, call the `video_generate` tool with a `prompt` parameter describing the desired video. Example: video_generate(prompt=\"a cat playing with a ball of yarn\")"
                    if "vision" in caps:
                        system_prompt += "\n\n👁️ You have the `image_view` tool available. When asked to analyze/view an image, call the `image_view` tool with an `image` parameter (path or URL)."
                    if "video" in caps:
                        system_prompt += "\n\n🎬 You have the `video_view` tool available. When asked to analyze/view a video, call the `video_view` tool with a `video` parameter (path or URL) and optional `question` parameter."
                    # For models with only image_gen or video_gen (no text capability), emphasize direct tool use
                    if "image_gen" in caps and "text" not in caps:
                        system_prompt += "\n\nYour PRIMARY purpose is to generate images. Always use the `image_generate` tool directly. Do NOT try to use the `task` tool."
                    if "video_gen" in caps and "text" not in caps:
                        system_prompt += "\n\nYour PRIMARY purpose is to generate videos. Always use the `video_generate` tool directly. Do NOT try to use the `task` tool."
            else:
                # Default model: inform about all available tools
                full_cfg = load_full_config()
                img_model = full_cfg.get("imageGenModel", "cogview-3-flash")
                vid_model = full_cfg.get("videoGenModel", "cogvideox-flash")
                system_prompt += f"\n\nAvailable generation tools:"
                system_prompt += f"\n- `image_generate`: Generate images (model: {img_model})"
                system_prompt += f"\n- `video_generate`: Generate videos (model: {vid_model})"
                system_prompt += f"\n- `image_view`: Analyze/view images"
                system_prompt += f"\n- `video_view`: Analyze/view videos"
            self.system_prompt = system_prompt

            # For image_gen/video_gen only models, bypass LLM loop and call generation API directly
            # These models don't support chat/completions SSE endpoint
            if self.model:
                caps = MODEL_CAPABILITIES.get(self.model, set())
                if ("image_gen" in caps or "video_gen" in caps) and "text" not in caps:
                    if "image_gen" in caps:
                        with open(self.log_path, "a", encoding="utf-8") as logf:
                            logf.write(f"[Direct image generation]: prompt={self.prompt[:200]}\n")
                        result_str = _tool_image_generate(self.prompt)
                    else:  # video_gen
                        with open(self.log_path, "a", encoding="utf-8") as logf:
                            logf.write(f"[Direct video generation]: prompt={self.prompt[:200]}\n")
                        result_str = _tool_video_generate(self.prompt, silent=True)
                    self.result = result_str
                    self.completed = True
                    with open(self.log_path, "a", encoding="utf-8") as logf:
                        logf.write(f"[Result]:\n{result_str[:2000]}\n\n")
                        logf.write("=" * 50 + "\n")
                        logf.write(f"Completed: {datetime.now().isoformat()}\n")
                    with _subagent_lock:
                        _completed_subagents[self.subagent_id] = self
                        if len(_completed_subagents) > _COMPLETED_SUBAGENTS_MAX:
                            oldest_ids = sorted(_completed_subagents.keys())[:len(_completed_subagents) - _COMPLETED_SUBAGENTS_MAX]
                            for old_id in oldest_ids:
                                del _completed_subagents[old_id]
                    return

            # If resuming, continue from previous messages; otherwise start fresh
            if self._is_resume and self._resume_messages:
                messages = list(self._resume_messages)
                messages.append({"role": "user", "content": self.prompt})
            else:
                messages = [{"role": "user", "content": self.prompt}]

            # Run the sub-agent loop (limited rounds, no task tool)
            max_rounds = 10
            round_num = 0
            final_text = ""

            while round_num < max_rounds:
                round_num += 1
                _llm_result = call_llm_direct(
                    messages, system_prompt=system_prompt, model=self.model, use_tools=True, is_subagent=True,
                    thinking_log_path=self.log_path
                )
                text, tool_calls, error, already_displayed = _llm_result[:4]

                if error:
                    self.error = error
                    break

                if not text and not tool_calls:
                    break

                if text and text.strip():
                    final_text = text.strip()
                    messages.append({"role": "assistant", "content": final_text})

                    with open(self.log_path, "a", encoding="utf-8") as logf:
                        logf.write(f"[Assistant round {round_num}]:\n{final_text}\n\n")

                if tool_calls:
                    for tc in tool_calls:
                        name = tc.get("name", "")
                        args = tc.get("args", {})
                        tc_id = tc.get("id", "")

                        # Sub-agents cannot spawn further sub-agents
                        if name == "task":
                            result_str = "Sub-agents cannot spawn further sub-agents."
                        else:
                            result_str = execute_tool(name, args)

                        with open(self.log_path, "a", encoding="utf-8") as logf:
                            args_display = json.dumps(args, ensure_ascii=False)
                            if len(args_display) > 200:
                                args_display = args_display[:200] + "..."
                            logf.write(f"[Tool: {name}({args_display})]:\n")
                            # Also write the tool result separately so viewer can render it
                            result_for_log = result_str[:2000] if len(result_str) > 2000 else result_str
                            logf.write(f"[Tool Result: {name}]:\n{result_for_log}\n\n")

                        # Store assistant message with tool call info for API round-trip
                        if tc_id:
                            messages.append({
                                "role": "assistant",
                                "content": text or "",
                                "tool_calls": [{
                                    "id": tc_id, "type": "function",
                                    "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}
                                }],
                                "_raw_api_msg": {
                                    "role": "assistant",
                                    "content": text or "",
                                    "tool_calls": [{
                                        "id": tc_id, "type": "function",
                                        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}
                                    }]
                                }
                            })
                        else:
                            messages.append({"role": "assistant", "content": text or ""})
                        messages.append({"role": "tool", "content": result_str, "tool_call_id": tc_id})
                    continue
                else:
                    break

            # Error must not be masked as success: the agent loop checks
            # result BEFORE error, so a non-None result hides self.error
            if not self.error:
                self.result = final_text or "(Sub-agent completed with no text output)"
                self.messages = messages  # Save for potential resume
            self.completed = True

            with open(self.log_path, "a", encoding="utf-8") as logf:
                logf.write("=" * 50 + "\n")
                logf.write(f"Completed: {datetime.now().isoformat()}\n")
                if self.result:
                    logf.write(f"Result: {self.result[:500]}\n")
                elif self.error:
                    logf.write(f"ERROR: {self.error[:500]}\n")

            # Store in completed sub-agents for potential resume
            if self.result:
                with _subagent_lock:
                    _completed_subagents[self.subagent_id] = self
                    # Evict oldest entries if over limit
                    if len(_completed_subagents) > _COMPLETED_SUBAGENTS_MAX:
                        oldest_ids = sorted(_completed_subagents.keys())[:len(_completed_subagents) - _COMPLETED_SUBAGENTS_MAX]
                        for old_id in oldest_ids:
                            del _completed_subagents[old_id]

        except Exception as e:
            self.error = str(e)
            self.completed = True
            with open(self.log_path, "a", encoding="utf-8") as logf:
                logf.write(f"\nERROR: {e}\n{traceback.format_exc()}\n")

    def start(self):
        """Start the sub-agent in a background thread."""
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def wait(self, timeout=None):
        """Wait for the sub-agent to complete."""
        if self.thread:
            self.thread.join(timeout=timeout)

def _spawn_subagent(description, prompt, resume_subagent_id=None, subagent_model=None):
    """Spawn a sub-agent. Returns the SubAgentRunner instance or error string.
    If resume_subagent_id is provided, continue a previous sub-agent's conversation.
    subagent_model is REQUIRED — must be a configured sub-agent model name."""
    global _subagent_counter

    perms = get_permissions()
    max_sub = perms.get("maxSubAgents", 3)
    if max_sub == 0:
        return None, t("subagent.disabled")

    # Resolve sub-agent model from config — model is REQUIRED
    _lang = _get_lang()
    full_config = load_full_config()
    subagent_models = full_config.get("subagentModels", {})
    actual_model = None

    if subagent_model:
        if subagent_model in subagent_models:
            # It's a named sub-agent model from config
            actual_model = subagent_models[subagent_model]
        else:
            # Any direct model ID works too (registered or custom — endpoint and
            # API key are resolved via modelKeys/providerKeys/default routing).
            actual_model = subagent_model
    else:
        # No model specified — this is now an error
        if subagent_models:
            available_names = ", ".join(subagent_models.keys())
            return None, (f"调用子代理时必须指定模型名 (model参数)。可用的子代理模型名: {available_names}" if _lang == 'zh' else
                          f"Model name is REQUIRED when calling a sub-agent. Available sub-agent model names: {available_names}")
        else:
            return None, ("未配置任何子代理模型。请先使用 /config 配置子代理模型。" if _lang == 'zh' else
                          "No sub-agent models configured. Please use /config to set up sub-agent models first.")

    with _subagent_lock:
        # Check concurrent limit
        active_count = sum(1 for sa in _active_subagents.values() if not sa.completed)
        if active_count >= max_sub:
            return None, t("subagent.queued", desc=description)

        # If resuming, reuse the same sub-agent ID (not increment counter)
        if resume_subagent_id is not None:
            sa_id = resume_subagent_id
        else:
            _subagent_counter += 1
            sa_id = _subagent_counter

        runner = SubAgentRunner(description, prompt, sa_id, model=actual_model)

        # If resuming, load previous sub-agent's messages
        if resume_subagent_id is not None:
            prev_runner = _completed_subagents.get(resume_subagent_id)
            if prev_runner and prev_runner.messages:
                runner._is_resume = True
                runner._resume_messages = prev_runner.messages
                runner.system_prompt = prev_runner.system_prompt
                # Reuse the same log file (append mode)
                runner.log_path = prev_runner.log_path
                # Remove from completed so it can be re-tracked as active
                del _completed_subagents[resume_subagent_id]
            else:
                return None, f"Sub-agent {resume_subagent_id} not found or has no conversation history"

        _active_subagents[sa_id] = runner

    runner.start()
    console.print(f"[dim]{t('subagent.log', path=runner.log_path)}[/dim]")
    return runner, None

def _check_subagents():
    """Check completed sub-agents and report results. Returns list of completed descriptions.
    Only shows brief completion message here; the full result is shown by the tool result panel."""
    completed = []
    with _subagent_lock:
        for sa_id, runner in list(_active_subagents.items()):
            if runner.completed and runner.result is not None:
                desc = runner.description
                elapsed = time.time() - runner._start_time
                if runner.error:
                    console.print(f"[danger]{t('subagent.failed', desc=desc)}: {runner.error[:100]}[/danger]")
                else:
                    console.print(f"[success]{t('subagent.completed', desc=desc)} ({elapsed:.1f}s)[/success]")
                    # NOTE: Do NOT show result Panel here - it's shown by the tool result display
                    # in the main agent loop (avoiding duplicate panels)
                completed.append(runner)
                del _active_subagents[sa_id]
            elif runner.completed and runner.error:
                desc = runner.description
                console.print(f"[danger]{t('subagent.failed', desc=desc)}: {runner.error[:100]}[/danger]")
                completed.append(runner)
                del _active_subagents[sa_id]
    return completed

def _spin_subagents(task_runners=None):
    """Show spinning animation while sub-agents are running, then return.
    If task_runners is provided, wait for those specific runners to complete.
    If None, just checks and returns immediately (non-blocking)."""
    if task_runners is None:
        _check_subagents()
        return

    # Wait for all specified sub-agents to finish with a spinner animation
    frame_idx = 0
    interrupted = False
    while True:
        # ESC: stop waiting immediately, sub-agents keep running in background
        if _check_esc_stop():
            interrupted = True
            _abort_active_responses()
            break
        # Check if all runners are done
        all_done = True
        active_descs = []
        for tc, runner in task_runners:
            if runner and not runner.completed:
                all_done = False
                # Check model capabilities for status hint
                model_hint = ""
                if runner.model:
                    caps = MODEL_CAPABILITIES.get(runner.model, set())
                    if "video_gen" in caps:
                        model_hint = " 🎬"
                    elif "image_gen" in caps:
                        model_hint = " 🎨"
                active_descs.append(runner.description[:20] + model_hint)
        if all_done:
            break
        # Show spinner frame with description of first active sub-agent
        frame = SPIN_FRAMES[frame_idx % len(SPIN_FRAMES)]
        desc = active_descs[0] if active_descs else "..."
        n_active = len(active_descs)
        suffix = f" +{n_active-1}" if n_active > 1 else ""
        sys.stderr.write(f"\r{frame} {desc}{suffix}... ")
        sys.stderr.flush()
        frame_idx += 1
        time.sleep(0.08)
    # Clear the spinner line
    sys.stderr.write("\r" + " " * 60 + "\r")
    sys.stderr.flush()
    if interrupted:
        # Still collect runners that already finished — skipping this leaves
        # their results unconsumed and their concurrency slots occupied forever
        _check_subagents()
        return
    # Report completed sub-agents
    _check_subagents()

# ── §8d BASH TOOL ─────────────────────────────────────────────
_cfg_workdir = load_full_config().get("workDir", "")
if _cfg_workdir and os.path.isdir(_cfg_workdir):
    _cwd = os.path.abspath(_cfg_workdir)
    os.chdir(_cwd)
else:
    _cwd = os.getcwd()

def get_cwd():
    return _cwd

def set_cwd(p):
    global _cwd
    try:
        os.chdir(p)
        _cwd = os.getcwd()
    except Exception as e:
        console.print(f"[danger]cd failed: {e}[/danger]")

# File read tracking for safety (read-before-edit guard)
# Uses (path -> read_timestamp) with LRU eviction and expiry
_file_read_times = {}
_FILE_READ_MAX_ENTRIES = 500
_FILE_READ_EXPIRY_SECONDS = 3600  # 1 hour

def _cleanup_file_read_times():
    """Evict expired and excess entries from _file_read_times."""
    global _file_read_times
    now = time.time()
    # Remove expired entries
    _file_read_times = {
        k: v for k, v in _file_read_times.items()
        if now - v < _FILE_READ_EXPIRY_SECONDS
    }
    # If still too many, keep most recent
    if len(_file_read_times) > _FILE_READ_MAX_ENTRIES:
        sorted_items = sorted(_file_read_times.items(), key=lambda x: x[1], reverse=True)
        _file_read_times = dict(sorted_items[:_FILE_READ_MAX_ENTRIES])

def _record_file_read(filepath):
    """Record that a file was read (for read-before-edit safety check)."""
    try:
        abs_path = os.path.abspath(str(filepath))
        _file_read_times[abs_path] = time.time()
        # Periodic cleanup
        if len(_file_read_times) > _FILE_READ_MAX_ENTRIES * 1.2:
            _cleanup_file_read_times()
    except Exception:
        pass

def _was_file_read(filepath):
    """Check if a file was read recently (for safety check).
    Returns False if the read record has expired (>1 hour old),
    or if the file was modified since it was last read."""
    abs_path = os.path.abspath(str(filepath))
    read_time = _file_read_times.get(abs_path)
    if read_time is None:
        return False
    # Check if the read record is still fresh
    if time.time() - read_time > _FILE_READ_EXPIRY_SECONDS:
        del _file_read_times[abs_path]
        return False
    # Check if file was modified since last read
    try:
        mtime = os.path.getmtime(abs_path)
        if mtime > read_time:
            # File was modified externally, record is stale
            del _file_read_times[abs_path]
            return False
    except (OSError, IOError):
        pass
    return True

# ── §8a  BASH TOOL (Cross-platform shell) ─────────────────────

def _tool_bash(command, timeout=30):
    """Execute shell command with cross-platform auto-adaptation."""
    perms = get_permissions()
    if not perms.get("allowExecute", True):
        return t("cmd.perm_denied", action="execute")
    if not is_command_allowed(command):
        return t("cmd.perm_denied", action=f"blocked command: {command}")

    # Dangerous command check — require confirmation (only if confirmDangerousCommands is enabled)
    if perms.get("confirmDangerousCommands", True) and is_dangerous_command(command):
        # Only the main thread can prompt on stdin. A sub-agent thread calling
        # input() here would steal the main loop's user input (deadlock/garbage).
        if threading.current_thread() is not threading.main_thread():
            return t("cmd.dangerous_blocked", cmd=command[:80])
        console.print(f"[danger]{t('cmd.dangerous', cmd=command[:80])}[/danger]")
        try:
            confirm = input(t("cmd.dangerous_confirm")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            confirm = ""
        if confirm != "y":
            return t("cmd.dangerous_blocked", cmd=command[:80])
        console.print(f"[warning]{t('cmd.dangerous_confirmed')}[/warning]")

    timeout = min(max(timeout, 1), 300)
    max_output = perms.get("maxCommandOutputSize", 30000)
    if max_output is None:
        max_output = float('inf')

    # Adapt command for current platform
    adapted_cmd = _adapt_command_for_shell(command)

    try:
        # Build the full command
        if IS_WINDOWS:
            if SHELL_NAME == "powershell":
                full_cmd = [SHELL_PATH] + SHELL_ARGS + [adapted_cmd]
            else:
                full_cmd = [SHELL_PATH] + SHELL_ARGS + [adapted_cmd]
        else:
            full_cmd = [SHELL_PATH] + SHELL_ARGS + [adapted_cmd]

        result = subprocess.run(
            full_cmd,
            capture_output=True, text=True,
            timeout=timeout,
            cwd=get_cwd(),
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            # Chinese Windows shells emit GBK (cp936); decoding as utf-8 garbles
            # all non-ASCII output. Use the OS preferred encoding instead.
            encoding=locale.getpreferredencoding(False) or 'utf-8',
            errors='replace',
        )

        stdout = result.stdout or ""
        stderr = result.stderr or ""
        exit_code = result.returncode

        output = ""
        if stdout:
            output += stdout
        if stderr:
            output += ("\n--- stderr ---\n" if stdout else "") + stderr

        if exit_code != 0:
            output += f"\n[exit code: {exit_code}]"

        if max_output is not float('inf') and len(output) > max_output:
            half = int(max_output) // 2
            output = output[:half] + f"\n\n... ({len(output) - int(max_output)} chars truncated) ...\n\n" + output[-half:]

        return output or "(no output)"

    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s"
    except FileNotFoundError:
        return f"Shell not found: {SHELL_PATH}"
    except Exception as e:
        return f"Command error: {e}"

# ── §8b  READ TOOL (File/directory reading with line numbers) ──

def _tool_read(path, offset=1, limit=2000):
    """Read file or directory with line numbers and encoding detection."""
    perms = get_permissions()
    if not perms.get("allowRead", True):
        return t("cmd.perm_denied", action="read")
    if not is_path_allowed(path):
        return t("cmd.perm_denied", action=f"blocked path: {path}")

    path = _safe_path(path)
    max_size = perms.get("maxFileReadSize", 50000)
    if max_size is None:
        max_size = float('inf')

    try:
        abs_path = os.path.abspath(os.path.join(get_cwd(), path))
        if not os.path.exists(abs_path):
            # Try to suggest similar files
            parent = os.path.dirname(abs_path)
            name = os.path.basename(abs_path)
            if os.path.isdir(parent):
                siblings = [f for f in os.listdir(parent) if name.lower() in f.lower()]
                if siblings:
                    return f"File not found: {path}\nDid you mean: {', '.join(siblings[:5])}?"
            return f"File not found: {path}"

        if os.path.isdir(abs_path):
            # List directory contents
            entries = sorted(os.listdir(abs_path))
            lines = []
            for entry in entries:
                full = os.path.join(abs_path, entry)
                if os.path.isdir(full):
                    lines.append(f"  📁 {entry}/")
                else:
                    try:
                        size = os.path.getsize(full)
                        if size > 1024 * 1024:
                            size_str = f"{size // (1024*1024)}MB"
                        elif size > 1024:
                            size_str = f"{size // 1024}KB"
                        else:
                            size_str = f"{size}B"
                        lines.append(f"  📄 {entry} ({size_str})")
                    except Exception:
                        lines.append(f"  📄 {entry}")
            _record_file_read(abs_path)
            return f"📂 {path}/ ({len(entries)} entries)\n" + "\n".join(lines)

        # Read file
        size_bytes = os.path.getsize(abs_path)
        # max_size is in characters, not bytes. UTF-8 Chinese is ~3 bytes/char.
        # Use a rough conversion: allow up to max_size * 3 bytes
        if size_bytes > max_size * 3:
            return f"File too large: {path} ({size_bytes} bytes, ~{size_bytes // 3} chars, max {'∞' if max_size == float('inf') else max_size} chars)"

        encoding = _detect_encoding(abs_path)
        with open(abs_path, "r", encoding=encoding, errors="replace") as f:
            all_lines = f.readlines()

        # Apply offset and limit
        start = max(0, offset - 1)
        end = min(len(all_lines), start + limit)
        selected = all_lines[start:end]

        # Format with line numbers
        output_lines = []
        max_line_num = end
        num_width = len(str(max_line_num))
        for i, line in enumerate(selected):
            line_num = start + i + 1
            # Strip \r for display
            line = line.rstrip('\r\n')
            # Truncate very long lines
            if len(line) > 500:
                line = line[:500] + "..."
            output_lines.append(f"{line_num:>{num_width}} │ {line}")

        _record_file_read(abs_path)
        header = f"📄 {path} ({len(all_lines)} lines, {encoding})"
        if start > 0 or end < len(all_lines):
            header += f" [lines {start+1}-{end}]"
        return header + "\n" + "\n".join(output_lines)

    except Exception as e:
        return f"Read error: {e}"

# ── §8c  WRITE TOOL (File writing with auto line endings) ──────

def _tool_write(path, content):
    """Write content to file with auto line endings and parent dir creation."""
    perms = get_permissions()
    if not perms.get("allowWrite", True):
        return t("cmd.perm_denied", action="write")
    if not is_path_allowed(path):
        return t("cmd.perm_denied", action=f"blocked path: {path}")

    path = _safe_path(path)
    max_size = perms.get("maxFileWriteSize", 200000)
    if max_size is None:
        max_size = float('inf')

    if len(content) > max_size:
        return f"Content too large: {len(content)} chars (max {'∞' if max_size == float('inf') else max_size})"

    try:
        abs_path = os.path.abspath(os.path.join(get_cwd(), path))

        # Create parent directories
        parent = os.path.dirname(abs_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        # Normalize line endings for current platform
        content = _normalize_line_endings(content)

        with open(abs_path, "w", encoding="utf-8", newline='') as f:
            f.write(content)

        lines = content.count('\n') + (1 if content and not content.endswith('\n') else 0)
        return f"✅ Written {len(content)} chars ({lines} lines) to {path}"

    except Exception as e:
        return f"Write error: {e}"

# ── §8d  EDIT TOOL (5 matching strategies) ─────────────────────

def _tool_edit(path, new_string, old_string="", strategy="exact",
               start_line=None, end_line=None, prefix="", suffix="",
               pattern="", replace_all=False):
    """Edit a file using one of 5 matching strategies."""
    perms = get_permissions()
    if not perms.get("allowWrite", True):
        return t("cmd.perm_denied", action="write")
    if not is_path_allowed(path):
        return t("cmd.perm_denied", action=f"blocked path: {path}")

    path = _safe_path(path)

    try:
        abs_path = os.path.abspath(os.path.join(get_cwd(), path))
        if not os.path.isfile(abs_path):
            return f"File not found: {path}"

        # Safety check: file must have been read first
        if not _was_file_read(abs_path):
            # File was not previously read - record it now but add warning
            _record_file_read(abs_path)
            _safety_warning = "⚠️ File was not read before editing. Content may have changed externally.\n"

        encoding = _detect_encoding(abs_path)
        with open(abs_path, "r", encoding=encoding, errors="replace") as f:
            content = f.read()

        new_content = None
        match_count = 0
        strategy_label = strategy

        if strategy == "exact":
            # Strategy 1: Exact byte-for-byte match
            if not old_string:
                return "old_string is required for 'exact' strategy"
            count = content.count(old_string)
            if count == 0:
                return t("edit.not_found", strategy=t("edit.strategy_exact"))
            if count > 1 and not replace_all:
                return t("edit.multiple_matches", count=count)
            if old_string == new_string:
                return t("edit.no_changes")
            if replace_all:
                new_content = content.replace(old_string, new_string)
                match_count = count
            else:
                new_content = content.replace(old_string, new_string, 1)
                match_count = 1
            strategy_label = t("edit.strategy_exact")

        elif strategy == "line":
            # Strategy 2: Line number range replacement
            if start_line is None:
                return "start_line is required for 'line' strategy"
            lines = content.split('\n')
            start = max(0, start_line - 1)
            end = end_line if end_line else start + 1
            if end > len(lines):
                end = len(lines)
            if start >= len(lines):
                return f"start_line {start_line} exceeds file length ({len(lines)} lines)"
            old_lines = '\n'.join(lines[start:end])
            new_lines = _normalize_line_endings(new_string)
            lines[start:end] = new_lines.split('\n')
            new_content = '\n'.join(lines)
            match_count = end - start
            strategy_label = t("edit.strategy_line")

        elif strategy == "prefix_suffix":
            # Strategy 3: Match content between prefix and suffix
            if not prefix and not suffix:
                return "prefix or suffix is required for 'prefix_suffix' strategy"
            pre_idx = content.find(prefix) if prefix else 0
            if prefix and pre_idx == -1:
                return t("edit.not_found", strategy=t("edit.strategy_prefix_suffix"))
            suf_idx = content.find(suffix, pre_idx + len(prefix)) if suffix else len(content)
            if suffix and suf_idx == -1:
                return t("edit.not_found", strategy=t("edit.strategy_prefix_suffix"))
            old_between = content[pre_idx + len(prefix):suf_idx]
            if old_between == new_string:
                return t("edit.no_changes")
            new_content = content[:pre_idx + len(prefix)] + new_string + content[suf_idx:]
            match_count = 1
            strategy_label = t("edit.strategy_prefix_suffix")

        elif strategy == "regex":
            # Strategy 4: Regex pattern match
            if not pattern:
                return "pattern is required for 'regex' strategy"
            try:
                flags = re.MULTILINE | re.DOTALL
                compiled = re.compile(pattern, flags)
            except re.error as e:
                return f"Invalid regex: {e}"
            matches = list(compiled.finditer(content))
            if not matches:
                return t("edit.not_found", strategy=t("edit.strategy_regex"))
            if len(matches) > 1 and not replace_all:
                return t("edit.multiple_matches", count=len(matches))
            if replace_all:
                new_content = compiled.sub(lambda m: new_string, content)
                match_count = len(matches)
            else:
                new_content = compiled.sub(lambda m: new_string, content, count=1)
                match_count = 1
            strategy_label = t("edit.strategy_regex")

        elif strategy == "fuzzy":
            # Strategy 5: Whitespace-tolerant match (3-tier cascade)
            if not old_string:
                return "old_string is required for 'fuzzy' strategy"
            # Try 3 levels of matching
            found_idx = -1
            fuzz_level = -1
            match_line_start = -1  # for level 1/2: matched line window start

            # Level 1: Exact match
            idx = content.find(old_string)
            if idx != -1:
                found_idx = idx
                fuzz_level = 0
            else:
                # Level 2: Trim right whitespace
                old_trimmed_r = old_string.rstrip()
                content_lines = content.split('\n')
                old_lines = old_string.split('\n')
                for i in range(len(content_lines) - len(old_lines) + 1):
                    segment = '\n'.join(content_lines[i:i + len(old_lines)])
                    if segment.rstrip() == old_trimmed_r:
                        found_idx = len('\n'.join(content_lines[:i])) + (1 if i > 0 else 0)
                        fuzz_level = 1
                        match_line_start = i
                        break

                if found_idx == -1:
                    # Level 3: Trim all whitespace
                    old_stripped = '\n'.join(line.strip() for line in old_string.split('\n'))
                    for i in range(len(content_lines) - len(old_lines) + 1):
                        segment = '\n'.join(line.strip() for line in content_lines[i:i + len(old_lines)])
                        if segment == old_stripped:
                            found_idx = len('\n'.join(content_lines[:i])) + (1 if i > 0 else 0)
                            fuzz_level = 2
                            match_line_start = i
                            break

            if found_idx == -1:
                return t("edit.not_found", strategy=t("edit.strategy_fuzzy"))

            if fuzz_level == 0:
                # Exact match: slice boundaries are reliable
                new_content = content[:found_idx] + new_string + content[found_idx + len(old_string):]
            else:
                # Level 1/2: the actual matched text differs in length from
                # old_string (whitespace trimmed), so slicing by len(old_string)
                # would cut the file at the wrong offset. Replace by line range.
                content_lines[match_line_start:match_line_start + len(old_lines)] = _normalize_line_endings(new_string).split('\n')
                new_content = '\n'.join(content_lines)
            match_count = 1
            fuzz_label = ["exact", "trim-right", "trim-all"][fuzz_level]
            strategy_label = f"{t('edit.strategy_fuzzy')} (fuzz={fuzz_label})"
        else:
            return f"Unknown strategy: {strategy}. Use: exact, line, prefix_suffix, regex, fuzzy"

        if new_content is None:
            return "Edit failed: no replacement made"

        if new_content == content:
            return t("edit.no_changes")

        # Generate diff for feedback
        old_lines = content.splitlines()
        new_lines = new_content.splitlines()
        diff_lines = list(difflib.unified_diff(old_lines, new_lines, lineterm='', n=3))
        diff_text = '\n'.join(diff_lines[:50])
        if len(diff_lines) > 50:
            diff_text += f"\n... ({len(diff_lines) - 50} more diff lines)"

        # Write the file
        new_content = _normalize_line_endings(new_content)
        # Pre-verify encodability: a char missing from the file's original
        # charset would raise UnicodeEncodeError mid-write and truncate the file
        try:
            new_content.encode(encoding)
        except (UnicodeEncodeError, LookupError):
            encoding = "utf-8"
        with open(abs_path, "w", encoding=encoding, newline='') as f:
            f.write(new_content)

        # Update read tracking
        _record_file_read(abs_path)

        _safety = locals().get('_safety_warning', '')
        return t("edit.success", strategy=strategy_label, count=match_count) + "\n\n" + diff_text + ("\n" + _safety if _safety else "")

    except Exception as e:
        return f"Edit error: {e}"

# ── §8e  GLOB TOOL (File pattern matching) ─────────────────────

def _tool_glob(pattern, path="."):
    """Find files matching a glob pattern."""
    abs_base = os.path.abspath(os.path.join(get_cwd(), path))
    if not is_path_allowed(abs_base):
        return t("cmd.perm_denied", action=f"blocked path: {path}")
    try:
        # Try using ripgrep first
        rg_path = shutil.which("rg")
        if rg_path:
            try:
                result = subprocess.run(
                    [rg_path, "--files", "-L", "--null", "--glob", pattern],
                    capture_output=True, text=True, timeout=15,
                    cwd=abs_base
                )
                if result.returncode == 0:
                    files = [f for f in result.stdout.strip('\x00').split('\x00') if f]
                    files = sorted(files, key=len)[:100]
                    if files:
                        return "\n".join(f"📄 {f}" for f in files)
            except Exception:
                pass

        # Fallback: Python glob
        matches = sorted(_glob_mod.glob(os.path.join(abs_base, pattern), recursive=True), key=lambda x: len(str(x)))
        if not matches:
            return f"No files matching: {pattern}"
        matches = matches[:100]
        return "\n".join(f"📄 {os.path.relpath(m, abs_base)}" for m in matches)

    except Exception as e:
        return f"Glob error: {e}"

# ── §8f  GREP TOOL (Content search) ────────────────────────────

def _tool_grep(pattern, path=".", include="*", literal=False, case_insensitive=False, max_results=100):
    """Search for text/regex patterns in files."""
    abs_path = os.path.abspath(os.path.join(get_cwd(), path))
    if not is_path_allowed(abs_path):
        return t("cmd.perm_denied", action=f"blocked path: {path}")
    if literal:
        # Escape regex special chars
        for ch in r'\.+*?()[]{}^$|':
            pattern = pattern.replace(ch, '\\' + ch)

    try:
        flags = 0
        if case_insensitive:
            flags |= re.IGNORECASE
        compiled = re.compile(pattern, flags)

        # Convert include glob to regex
        include_re = None
        if include and include != "*":
            inc_pattern = include.replace('.', r'\.').replace('*', '.*').replace('?', '.')
            if '{' in inc_pattern:
                # Handle *.{ts,tsx} patterns
                inc_pattern = inc_pattern.replace('{', '(').replace('}', ')').replace(',', '|')
            include_re = re.compile(inc_pattern, re.IGNORECASE)

        results = []
        # Skip common ignored directories
        skip_dirs = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'dist', 'build', '.next', '.cache', 'vendor'}

        if os.path.isfile(abs_path):
            files = [abs_path]
        else:
            files = []
            for root, dirs, filenames in os.walk(abs_path):
                dirs[:] = [d for d in dirs if d not in skip_dirs]
                for fn in filenames:
                    if include_re:
                        # fullmatch: "*.py" must not match "x.py.bak" via partial match
                        if not include_re.fullmatch(fn):
                            continue
                    files.append(os.path.join(root, fn))
                    if len(files) > 5000:
                        break

        # Try ripgrep first
        rg_path = shutil.which("rg")
        if rg_path and os.path.isdir(abs_path):
            try:
                rg_cmd = [rg_path, "-H", "-n", "--max-count", str(max_results)]
                if case_insensitive:
                    rg_cmd.append("-i")
                if include and include != "*":
                    rg_cmd.extend(["--glob", include])
                rg_cmd.extend([pattern, abs_path])
                result = subprocess.run(rg_cmd, capture_output=True, text=True, timeout=15)
                if result.returncode == 0 and result.stdout.strip():
                    lines = result.stdout.strip().split('\n')[:max_results]
                    return '\n'.join(lines)
            except Exception:
                pass

        # Fallback: pure Python search
        for filepath in files:
            if len(results) >= max_results:
                break
            try:
                enc = _detect_encoding(filepath)
                with open(filepath, "r", encoding=enc, errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if compiled.search(line):
                            rel = os.path.relpath(filepath, abs_path) if os.path.isdir(abs_path) else filepath
                            line_clean = line.rstrip('\r\n')
                            if len(line_clean) > 200:
                                line_clean = line_clean[:200] + "..."
                            results.append(f"{rel}:{i}: {line_clean}")
                            if len(results) >= max_results:
                                break
            except Exception:
                continue

        if not results:
            return f"No matches for: {pattern}"
        return '\n'.join(results)

    except re.error as e:
        return f"Invalid regex: {e}"
    except Exception as e:
        return f"Grep error: {e}"

# ── §8g  WEB SEARCH ────────────────────────────────────────────

def _do_web_search(query, num_results=5):
    """Perform web search using ddgs, with Bing fallback."""
    try:
        # Strategy 1: ddgs package (new name, preferred)
        try:
            from ddgs import DDGS as DDGS1
            with DDGS1() as ddgs:
                search_results = list(ddgs.text(query, max_results=num_results))
            if search_results:
                results = []
                for r in search_results:
                    title = r.get("title", "")
                    href = r.get("href", "")
                    body = r.get("body", "")
                    if title:
                        results.append(f"📄 {title}\n   🔗 {href}\n   {body}")
                if results:
                    header = f"🔍 Search results for: {query}\n{'='*50}\n\n"
                    return header + "\n\n".join(results)
        except ImportError:
            pass
        except Exception:
            pass

        # Strategy 2: duckduckgo_search (old package name, shows warning)
        try:
            from duckduckgo_search import DDGS as DDGS2
            with DDGS2() as ddgs:
                search_results = list(ddgs.text(query, max_results=num_results))
            if search_results:
                results = []
                for r in search_results:
                    title = r.get("title", "")
                    href = r.get("href", "")
                    body = r.get("body", "")
                    if title:
                        results.append(f"📄 {title}\n   🔗 {href}\n   {body}")
                if results:
                    header = f"🔍 Search results for: {query}\n{'='*50}\n\n"
                    return header + "\n\n".join(results)
        except ImportError:
            pass
        except Exception:
            pass

        # Strategy 3: Bing fallback
        import urllib.parse
        import urllib.request
        encoded_query = urllib.parse.quote(query)
        search_url = f"https://www.bing.com/search?q={encoded_query}"
        req = urllib.request.Request(search_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "en-US,en;q=0.5",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        results = []
        bing_title = re.compile(r'<h2><a[^>]*href="([^"]*)"[^>]*>(.*?)</a></h2>', re.DOTALL)
        bing_snippet = re.compile(r'<p[^>]*class="b_caption"[^>]*>(.*?)</p>', re.DOTALL)
        btitles = bing_title.findall(html)
        bsnippets = bing_snippet.findall(html)
        for i in range(min(len(btitles), num_results)):
            btitle = re.sub(r'<[^>]+>', '', btitles[i][1]).strip()
            burl = btitles[i][0]
            bsnippet = re.sub(r'<[^>]+>', '', bsnippets[i][0] if i < len(bsnippets) else "").strip()
            if btitle:
                results.append(f"📄 {btitle}\n   🔗 {burl}\n   {bsnippet}")

        if not results:
            return f"No search results found for: {query}\n\n💡 Tip: Install ddgs for better search: pip install ddgs"

        header = f"🔍 Search results for: {query}\n{'='*50}\n\n"
        return header + "\n\n".join(results)

    except Exception as e:
        return f"Search error: {e}"

# ── §8h  LEGACY/UTILITY TOOLS ─────────────────────────────────

def _tool_mkdir(path):
    perms = get_permissions()
    if not perms.get("allowWrite", True):
        return t("cmd.perm_denied", action="write")
    if not is_path_allowed(path):
        return t("cmd.perm_denied", action=f"blocked path: {path}")
    try:
        abs_path = os.path.abspath(os.path.join(get_cwd(), path))
        os.makedirs(abs_path, exist_ok=True)
        return f"✅ Created directory: {path}"
    except Exception as e:
        return f"mkdir error: {e}"

def _tool_rm(path):
    perms = get_permissions()
    if not perms.get("allowDelete", True):
        return t("cmd.perm_denied", action="delete")
    if not is_path_allowed(path):
        return t("cmd.perm_denied", action=f"blocked path: {path}")
    try:
        abs_path = os.path.abspath(os.path.join(get_cwd(), path))
        if os.path.isdir(abs_path):
            if os.path.islink(abs_path):
                # For symlink directories, only remove the link itself
                os.remove(abs_path)
            else:
                shutil.rmtree(abs_path)
        elif os.path.isfile(abs_path):
            os.remove(abs_path)
        else:
            return f"Path not found: {path}"
        return f"✅ Removed: {path}"
    except Exception as e:
        return f"rm error: {e}"

def _tool_git(args):
    return _tool_bash(f"git {args}")

def _tool_sysinfo():
    info = [f"🖥  OS: {platform.system()} {platform.release()} ({platform.machine()})"]
    info.append(f"🐍 Python: {platform.python_version()}")
    info.append(f"📁 CWD: {get_cwd()}")
    info.append(f"🐚 Shell: {SHELL_PATH} ({SHELL_NAME})")
    info.append(f"📦 ZAgent: {VERSION}")
    if psutil:
        mem = psutil.virtual_memory()
        info.append(f"💾 RAM: {mem.total//(1024*1024)}MB total, {mem.available//(1024*1024)}MB available")
        info.append(f"⚡ CPU: {psutil.cpu_percent()}% usage")
    return "\n".join(info)

def _tool_tree(path=".", depth=3):
    abs_path = os.path.abspath(os.path.join(get_cwd(), path))
    if not is_path_allowed(abs_path):
        return t("cmd.perm_denied", action=f"blocked path: {path}")
    if not os.path.isdir(abs_path):
        return f"Directory not found: {path}"
    lines = [f"📂 {path}/"]
    def _walk(dir_path, prefix="", d=0):
        if d >= depth:
            return
        try:
            entries = sorted(os.listdir(dir_path))
        except PermissionError:
            return
        dirs = [e for e in entries if os.path.isdir(os.path.join(dir_path, e))]
        files = [e for e in entries if os.path.isfile(os.path.join(dir_path, e))]
        skip = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'dist', 'build', '.next', '.cache'}
        dirs = [d for d in dirs if d not in skip]
        all_items = dirs + files
        for i, item in enumerate(all_items[:50]):
            is_last = (i == len(all_items) - 1) or i == 49
            connector = "└── " if is_last else "├── "
            full = os.path.join(dir_path, item)
            if os.path.isdir(full):
                lines.append(f"{prefix}{connector}📁 {item}/")
                _walk(full, prefix + ("    " if is_last else "│   "), d + 1)
            else:
                lines.append(f"{prefix}{connector}📄 {item}")
    _walk(abs_path)
    if len(lines) > 200:
        return '\n'.join(lines[:200]) + f"\n... ({len(lines) - 200} more entries truncated)"
    return '\n'.join(lines)

def _tool_cd(path):
    abs_path = os.path.abspath(os.path.join(get_cwd(), path))
    if not is_path_allowed(abs_path):
        return t("cmd.perm_denied", action=f"blocked path: {path}")
    if not os.path.isdir(abs_path):
        return f"Directory not found: {path}"
    set_cwd(abs_path)
    return f"📂 {get_cwd()}"

def _tool_pwd():
    return f"📂 {get_cwd()}"

# ── §8h  AI MEDIA TOOLS ─────────────────────────────────────────

def _encode_image_to_base64(image_path):
    """Encode a local image file to base64 string."""
    import base64
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def _get_image_media_type(image_path):
    """Get MIME type for an image file."""
    ext = Path(image_path).suffix.lower()
    media_types = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif",
        ".webp": "image/webp", ".bmp": "image/bmp",
    }
    return media_types.get(ext, "image/jpeg")

def _tool_image_view(image_path_or_url, question):
    """Analyze an image using a multimodal model.
    If main model supports vision, use it directly.
    Otherwise, use the configured multimodal model."""
    if not image_path_or_url:
        return t("image_view.no_image")

    config = load_zai_config()
    full_config = load_full_config()
    if not config:
        return t("llm.no_config")

    main_model = config.get("model", "glm-4-flash")

    # Determine which model to use
    if is_multimodal_model(main_model):
        model = main_model
    else:
        # Check if multimodal model is configured
        mm_model = full_config.get("multimodalModel", "")
        if mm_model:
            model = mm_model
            # Check fallback state for multimodal model
            use_backup, active_mm = model_fallback.should_use_backup_mm(full_config)
            if use_backup:
                model = active_mm
        else:
            return t("image_view.no_multimodal")

    # Multi-key routing for the selected vision model
    base_url, api_key = resolve_model_endpoint(model, full_config)

    console.print(f"[dim]{t('image_view.using_multimodal', model=model)}[/dim]")

    # Build image content for the API
    import urllib.request
    import urllib.error

    if image_path_or_url.startswith(("http://", "https://")):
        # URL-based image
        image_content = {
            "type": "image_url",
            "image_url": {"url": image_path_or_url}
        }
    else:
        # Local file - encode to base64
        image_path = Path(os.path.expanduser(image_path_or_url))
        if not image_path.exists():
            return f"Error: Image file not found: {image_path_or_url}"
        try:
            b64 = _encode_image_to_base64(str(image_path))
            media_type = _get_image_media_type(str(image_path))
            image_content = {
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{b64}"}
            }
        except Exception as e:
            return f"Error reading image: {e}"

    # Build API request
    url = f"{base_url}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    image_content,
                ]
            }
        ],
        "stream": False,
        "max_tokens": 2000,
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=60)
        result = json.loads(resp.read().decode("utf-8"))
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        if content:
            model_fallback.report_mm_success()
            return content
        return "(No response from vision model)"
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        if e.code == 429:
            should_switch, new_model = model_fallback.report_mm_error(full_config, is_429=True)
            if should_switch:
                backup = full_config.get("backupMultimodalModel", "")
                console.print(f"[yellow]{t('model.switch_backup', model=model, backup=backup)}[/yellow]")
                # Retry with backup model (re-resolve endpoint + key)
                body["model"] = new_model
                new_base, new_key = resolve_model_endpoint(new_model, full_config)
                new_url = f"{new_base}/chat/completions"
                headers = dict(headers)
                headers["Authorization"] = f"Bearer {new_key}"
                try:
                    req = urllib.request.Request(
                        new_url,
                        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                        headers=headers,
                        method="POST",
                    )
                    resp = urllib.request.urlopen(req, timeout=60)
                    result = json.loads(resp.read().decode("utf-8"))
                    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                    if content:
                        model_fallback.report_mm_success()
                        return content
                except Exception:
                    pass
            return t("image_view.no_multimodal") + f" (429 error)"
        return f"Error analyzing image: HTTP {e.code}: {err_body[:300]}"
    except Exception as e:
        return f"Error analyzing image: {e}"


def _tool_image_generate(prompt, size="1024x1024", output_path=""):
    """Generate an image using CogView-3-Flash or configured image generation model.
    Uses zhipuai SDK if available, falls back to direct HTTP API."""
    if not prompt:
        return "Error: no prompt specified for image generation"

    config = load_zai_config()
    full_config = load_full_config()
    if not config:
        return t("llm.no_config")

    # Determine image generation model
    img_model = full_config.get("imageGenModel", "cogview-3-flash")
    # Multi-key routing for this exact model / its provider / default
    base_url, api_key = resolve_model_endpoint(img_model, full_config)
    img_prov_id, _ = find_provider_for_model(img_model)

    # Preflight: a non-default provider with no dedicated key can never authorize
    if not provider_has_dedicated_key(img_model, full_config):
        return (f"⚠️ 模型 {img_model} 属于服务商 '{img_prov_id}'，但还没有配置该平台的 API Key。\n"
                f"请运行: /key {img_prov_id} <你的{img_prov_id} API Key>\n"
                f"(当前默认 Key 属于 '{_default_provider_id(full_config) or 'custom'}', 不能跨平台使用)")

    console.print(f"[dim]{t('image_gen.generating')} ({img_model} @ {img_prov_id or 'custom'})[/dim]")

    # --- Strategy 1: Use zhipuai SDK — ONLY for Zhipu GLM.
    # The SDK signs requests with a GLM-specific JWT derived from the key, so
    # using it against another OpenAI-compatible platform (e.g. Agnes) fails.
    if img_prov_id == "glm":
        try:
            from zhipuai import ZhipuAI
            try:
                client = ZhipuAI(api_key=api_key, base_url=base_url)
            except TypeError:
                # Older SDK versions don't accept base_url
                client = ZhipuAI(api_key=api_key)

            response = client.images.generations(
                model=img_model,
                prompt=prompt,
                size=size,
            )

            # Parse response
            if hasattr(response, 'data') and response.data:
                image_url = response.data[0].url if response.data[0] else ""
                if image_url:
                    return _save_image_result(image_url, prompt, img_model, size, output_path)
            return t("image_gen.fail", err="No image data in SDK response")

        except ImportError:
            # zhipuai SDK not available, fall through to HTTP API
            pass
        except Exception as e:
            # SDK failed, try HTTP API as fallback
            console.print(f"[dim]zhipuai SDK error: {e}, trying HTTP API...[/dim]")

    # --- Strategy 2: Direct HTTP API (works for ALL OpenAI-compatible platforms) ---
    import urllib.request
    import urllib.error

    # base_url/api_key already resolved via the multi-key router above
    url = f"{base_url}/images/generations"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    body = {
        "model": img_model,
        "prompt": prompt,
        "size": size,
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=120)
        result = json.loads(resp.read().decode("utf-8"))

        # Parse response - {"data": [{"url": "..."}]} or {"data": [{"b64_json": "..."}]}
        data = result.get("data", [])
        if not data:
            return t("image_gen.fail", err="No image data in response")

        image_url = data[0].get("url", "")
        b64 = data[0].get("b64_json", "")
        if not image_url and b64:
            import base64 as _b64
            if not output_path:
                import datetime
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                output_path = f"generated_image_{ts}.png"
            with open(output_path, "wb") as f:
                f.write(_b64.b64decode(b64))
            abs_path = str(Path(output_path).resolve())
            return t("image_gen.success", path=abs_path) + f"\nPrompt: {prompt}\nModel: {img_model}\nSize: {size}"
        if not image_url:
            return t("image_gen.fail", err="No image URL in response")

        return _save_image_result(image_url, prompt, img_model, size, output_path)

    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        hint = ""
        if e.code in (401, 403) and img_prov_id and img_prov_id != _default_provider_id(full_config):
            hint = f"\n提示: 该模型属于 '{img_prov_id}' 平台，请配置该平台自己的 Key: /key {img_prov_id} <KEY>"
        return t("image_gen.fail", err=f"HTTP {e.code}: {err_body[:300]}") + hint
    except Exception as e:
        return t("image_gen.fail", err=str(e))


def _save_image_result(image_url, prompt, img_model, size, output_path=""):
    """Save image generation result to file. Shared by SDK and HTTP paths."""
    import urllib.request
    if not output_path:
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"generated_image_{timestamp}.png"

    try:
        img_resp = urllib.request.urlopen(image_url, timeout=60)
        img_data = img_resp.read()
        with open(output_path, "wb") as f:
            f.write(img_data)
        abs_path = str(Path(output_path).resolve())
        return t("image_gen.success", path=abs_path) + f"\nPrompt: {prompt}\nModel: {img_model}\nSize: {size}"
    except Exception as e:
        # If download fails, return the URL
        return f"Image generated. URL: {image_url}\n(Download failed: {e})"


def _video_generate_agnes(vid_model, base_url, api_key, prompt, output_path="", silent=False):
    """Agnes AI video generation (agnes-video-v2.0).
    Submit: POST {base}/videos {model, prompt} → {video_id|id|task_id}
    Poll:   GET {root}/agnesapi?video_id=<id> → status completed/failed, metadata.url
    Legacy poll fallback: GET {base}/videos/{task_id}.
    Pure HTTP — Agnes keys are plain Bearer tokens (never the zhipuai JWT SDK)."""
    import urllib.request
    import urllib.error
    import urllib.parse

    def _log(msg):
        if not silent:
            console.print(f"[dim]{msg}[/dim]")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    base_url = base_url.rstrip("/")

    # 1) Submit
    try:
        req = urllib.request.Request(
            f"{base_url}/videos",
            data=json.dumps({"model": vid_model, "prompt": prompt}, ensure_ascii=False).encode("utf-8"),
            headers=headers, method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            created = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        hint = ""
        if e.code in (401, 403):
            hint = "\n提示: Agnes 是独立平台，请配置它自己的 Key: /key agnes <AGNES_API_KEY>"
        return t("video_gen.fail", err=f"HTTP {e.code}: {body[:300]}") + hint
    except Exception as e:
        return t("video_gen.fail", err=str(e))

    video_id = created.get("video_id") or created.get("id") or created.get("task_id") or ""
    if not video_id:
        return t("video_gen.fail", err=f"No video id in response: {json.dumps(created)[:200]}")
    _log(t("video_gen.generating") + f" ({vid_model}, id={video_id})")

    # Poll root host: https://apihub.agnes-ai.com/v1 -> https://apihub.agnes-ai.com
    root = base_url.split("/v1")[0] if "/v1" in base_url else base_url
    poll_url = f"{root}/agnesapi?video_id={urllib.parse.quote(str(video_id))}"
    legacy_url = f"{base_url}/videos/{urllib.parse.quote(str(video_id))}"

    max_poll_time = 600
    poll_interval = 10
    start_time = time.time()
    last_status = ""
    used_url = poll_url

    while time.time() - start_time < max_poll_time:
        if _check_esc_stop():
            return "ESC_STOP"
        time.sleep(poll_interval)
        try:
            req = urllib.request.Request(used_url, headers={"Authorization": f"Bearer {api_key}"}, method="GET")
            with urllib.request.urlopen(req, timeout=60) as resp:
                status_data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # Recommended endpoint may 404 for some accounts — switch once to legacy
            if used_url == poll_url and e.code in (404, 400):
                used_url = legacy_url
                continue
            _log(f"Poll error HTTP {e.code}, retrying...")
            continue
        except Exception as e:
            _log(f"Poll error: {e}, retrying...")
            continue

        task_status = (status_data.get("status")
                       or status_data.get("task_status")
                       or "").lower()
        progress = status_data.get("progress")
        if task_status and task_status != last_status:
            _log(t('video_gen.polling', status=task_status) + (f" {progress}%" if progress is not None else ""))
            last_status = task_status

        if task_status == "completed" or task_status == "success":
            metadata = status_data.get("metadata") or {}
            video_url = metadata.get("url") or status_data.get("url") or ""
            cover_url = metadata.get("cover_image_url") or status_data.get("cover_image_url") or ""
            if not video_url:
                vr = status_data.get("video_result") or []
                if vr and isinstance(vr, list):
                    video_url = vr[0].get("url", "")
                    cover_url = vr[0].get("cover_image_url", "")
            if video_url:
                return _save_video_result(video_url, cover_url, prompt, vid_model, output_path)
            return t("video_gen.fail", err="No video URL in completed response")
        if task_status == "failed" or task_status == "fail":
            err = status_data.get("error")
            return t("video_gen.fail", err=f"Task failed: {json.dumps(err, ensure_ascii=False)[:300] if err else 'unknown'}")

    return t("video_gen.timeout")


def _tool_video_generate(prompt, output_path="", silent=False):
    """Generate a video using CogVideoX-Flash or configured video generation model.
    This is an asynchronous operation - submit task, then poll for result.
    Uses zhipuai SDK if available, falls back to direct HTTP API.
    When silent=True, polling status is written to log instead of console (for sub-agents)."""
    if not prompt:
        return "Error: no prompt specified for video generation"

    config = load_zai_config()
    full_config = load_full_config()
    if not config:
        return t("llm.no_config")

    # Determine video generation model + multi-key routing
    vid_model = full_config.get("videoGenModel", "cogvideox-flash")
    base_url, api_key = resolve_model_endpoint(vid_model, full_config)
    vid_prov_id, vid_prov_info = find_provider_for_model(vid_model)

    # Preflight: non-default provider needs its own platform key
    if not provider_has_dedicated_key(vid_model, full_config):
        return (f"⚠️ 模型 {vid_model} 属于服务商 '{vid_prov_id}'，但还没有配置该平台的 API Key。\n"
                f"请运行: /key {vid_prov_id} <你的{vid_prov_id} API Key>\n"
                f"(当前默认 Key 属于 '{_default_provider_id(full_config) or 'custom'}', 不能跨平台使用)")

    # Agnes uses a completely different async protocol:
    #   submit POST {base}/videos  |  poll GET {root}/agnesapi?video_id=<id>
    if vid_prov_info and vid_prov_info.get("videoApi") == "agnes":
        return _video_generate_agnes(vid_model, base_url, api_key, prompt,
                                     output_path=output_path, silent=silent)

    if not silent:
        console.print(f"[dim]{t('video_gen.generating')} ({vid_model} @ {vid_prov_id or 'custom'})[/dim]")

    def _log_status(msg):
        """Print status to console or suppress based on silent mode."""
        if not silent:
            console.print(f"[dim]{msg}[/dim]")

    # --- Strategy 1: Use zhipuai SDK (recommended, handles auth properly) ---
    try:
        from zhipuai import ZhipuAI
        try:
            client = ZhipuAI(api_key=api_key, base_url=base_url)
        except TypeError:
            client = ZhipuAI(api_key=api_key)

        response = client.videos.generations(
            model=vid_model,
            prompt=prompt,
        )

        task_id = response.id if response else ""
        if not task_id:
            return t("video_gen.fail", err="No task ID in response")

        # Poll for completion using SDK
        max_poll_time = 600  # 10 minutes max
        poll_interval = 10  # Check every 10 seconds
        start_time = time.time()
        last_status = ""

        while time.time() - start_time < max_poll_time:
            if _check_esc_stop():
                return "ESC_STOP"

            time.sleep(poll_interval)

            try:
                poll_result = client.videos.retrieve_videos_result(id=task_id)
                task_status = poll_result.task_status
                # Only print status if it changed (reduces console spam)
                if task_status != last_status:
                    _log_status(t('video_gen.polling', status=task_status))
                    last_status = task_status

                if task_status == "SUCCESS":
                    if poll_result.video_result:
                        video_url = poll_result.video_result[0].url
                        cover_url = poll_result.video_result[0].cover_image_url if hasattr(poll_result.video_result[0], 'cover_image_url') else ""
                        return _save_video_result(video_url, cover_url, prompt, vid_model, output_path)
                    return t("video_gen.fail", err="No video data in successful response")

                elif task_status == "FAIL":
                    return t("video_gen.fail", err="Video generation task failed")

            except Exception as e:
                _log_status(f"Poll error: {e}, retrying...")
                continue

        return t("video_gen.timeout")

    except ImportError:
        # zhipuai SDK not available, fall through to HTTP API
        pass
    except Exception as e:
        # SDK failed, try HTTP API as fallback
        _log_status(f"zhipuai SDK error: {e}, trying HTTP API...")

    # --- Strategy 2: Direct HTTP API (fallback) ---
    import urllib.request
    import urllib.error

    # base_url/api_key already resolved via the multi-key router above
    url = f"{base_url}/videos/generations"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    body = {
        "model": vid_model,
        "prompt": prompt,
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=60)
        result = json.loads(resp.read().decode("utf-8"))

        task_id = result.get("id", "")
        if not task_id:
            return t("video_gen.fail", err="No task ID in response")

        # Poll for completion — use PATH parameter per API docs: /async-result/{id}
        poll_url = f"{base_url}/async-result/{task_id}"
        max_poll_time = 600  # 10 minutes max
        poll_interval = 10  # Check every 10 seconds
        start_time = time.time()
        last_status = ""

        while time.time() - start_time < max_poll_time:
            if _check_esc_stop():
                return "ESC_STOP"

            time.sleep(poll_interval)

            try:
                poll_req = urllib.request.Request(poll_url, headers=headers, method="GET")
                poll_resp = urllib.request.urlopen(poll_req, timeout=30)
                poll_result = json.loads(poll_resp.read().decode("utf-8"))

                task_status = poll_result.get("task_status", "")
                # Only print status if it changed (reduces console spam)
                if task_status != last_status:
                    _log_status(t('video_gen.polling', status=task_status))
                    last_status = task_status

                if task_status == "SUCCESS":
                    video_result = poll_result.get("video_result", [])
                    if video_result:
                        video_url = video_result[0].get("url", "")
                        cover_url = video_result[0].get("cover_image_url", "")
                        return _save_video_result(video_url, cover_url, prompt, vid_model, output_path)
                    return t("video_gen.fail", err="No video data in successful response")

                elif task_status == "FAIL":
                    fail_msg = poll_result.get("error", "Unknown error")
                    return t("video_gen.fail", err=fail_msg)

                # Still processing, continue polling

            except Exception as e:
                _log_status(f"Poll error: {e}, retrying...")
                continue

        return t("video_gen.timeout")

    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return t("video_gen.fail", err=f"HTTP {e.code}: {err_body[:300]}")
    except Exception as e:
        return t("video_gen.fail", err=str(e))


def _save_video_result(video_url, cover_url, prompt, vid_model, output_path=""):
    """Save video generation result to file. Shared by SDK and HTTP paths."""
    if not video_url:
        return t("video_gen.fail", err="No video URL in response")

    import urllib.request
    if not output_path:
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"generated_video_{timestamp}.mp4"

    try:
        vid_resp = urllib.request.urlopen(video_url, timeout=120)
        vid_data = vid_resp.read()
        with open(output_path, "wb") as f:
            f.write(vid_data)
        abs_path = str(Path(output_path).resolve())
        result_msg = t("video_gen.success", path=abs_path)
        result_msg += f"\nPrompt: {prompt}\nModel: {vid_model}"
        if cover_url:
            result_msg += f"\nCover: {cover_url}"
        return result_msg
    except Exception as e:
        return f"Video generated. URL: {video_url}\n(Download failed: {e})"


def _tool_video_view(video_path_or_url, question):
    """Analyze a video using a multimodal model that supports video understanding.
    Supports video URLs and local file paths. Uses models with 'video' capability."""
    if not video_path_or_url:
        return t("video_view.no_video")

    config = load_zai_config()
    full_config = load_full_config()
    if not config:
        return t("llm.no_config")

    main_model = config.get("model", "glm-4-flash")

    # Find a model that supports video understanding
    def _find_video_model():
        """Find the best model for video understanding."""
        # Check main model first
        if "video" in get_model_capabilities(main_model):
            return main_model
        # Check multimodal model
        mm_model = full_config.get("multimodalModel", "")
        if mm_model and "video" in get_model_capabilities(mm_model):
            # Check fallback state
            use_backup, active_mm = model_fallback.should_use_backup_mm(full_config)
            if use_backup:
                return active_mm
            return mm_model
        # Search sub-agent models
        sub_models = full_config.get("subagentModels", {})
        for name, mid in sub_models.items():
            if "video" in get_model_capabilities(mid):
                return mid
        # Fallback: check if multimodal model exists (even without explicit video cap)
        if mm_model and "vision" in get_model_capabilities(mm_model):
            use_backup, active_mm = model_fallback.should_use_backup_mm(full_config)
            if use_backup:
                return active_mm
            return mm_model
        return None

    model = _find_video_model()
    if not model:
        return t("video_view.no_multimodal")

    # Multi-key routing for the selected video model
    base_url, api_key = resolve_model_endpoint(model, full_config)

    console.print(f"[dim]{t('video_view.using_multimodal', model=model)}[/dim]")

    # Build video content for the API
    if video_path_or_url.startswith(("http://", "https://")):
        # URL-based video — use video_url content type
        video_content = {
            "type": "video_url",
            "video_url": {"url": video_path_or_url}
        }
    else:
        # Local file
        video_path = Path(os.path.expanduser(video_path_or_url))
        if not video_path.exists():
            return t("video_view.file_not_found", path=video_path_or_url)
        file_size_mb = video_path.stat().st_size / (1024 * 1024)
        if file_size_mb > 50:
            console.print(f"[yellow]{t('video_view.video_too_large', size=f'{file_size_mb:.1f}')}[/yellow]")
        # For GLM models, local video needs to be base64 encoded
        try:
            b64 = _encode_video_to_base64(str(video_path))
            media_type = _get_video_media_type(str(video_path))
            video_content = {
                "type": "video_url",
                "video_url": {"url": f"data:{media_type};base64,{b64}"}
            }
        except Exception as e:
            return f"Error reading video file: {e}"

    # Build messages with video content
    messages = [{
        "role": "user",
        "content": [
            video_content,
            {"type": "text", "text": question or "Describe what happens in this video in detail."}
        ]
    }]

    # Call the API
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    # Use zhipuai SDK if available (handles multimodal content better)
    try:
        from zhipuai import ZhipuAI
        try:
            client = ZhipuAI(api_key=api_key, base_url=base_url)
        except TypeError:
            client = ZhipuAI(api_key=api_key)

        response = client.chat.completions.create(
            model=model,
            messages=messages,
        )

        result_text = ""
        if hasattr(response, 'choices') and response.choices:
            result_text = response.choices[0].message.content
        elif hasattr(response, 'content'):
            result_text = response.content
        else:
            result_text = str(response)

        return result_text or "No analysis result returned."

    except ImportError:
        pass
    except Exception as e:
        # SDK failed, try HTTP API as fallback
        console.print(f"[dim]zhipuai SDK video view error: {e}, trying HTTP API...[/dim]")

    # Fallback: Direct HTTP API
    import urllib.request
    import urllib.error

    url = f"{base_url}/chat/completions"
    body = {
        "model": model,
        "messages": messages,
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=120)
        result = json.loads(resp.read().decode("utf-8"))
        result_text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        return result_text or "No analysis result returned."
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return f"Video analysis failed (HTTP {e.code}): {err_body[:300]}"
    except Exception as e:
        return f"Video analysis failed: {e}"


def _encode_video_to_base64(video_path):
    """Encode a video file to base64 string."""
    import base64
    with open(video_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _get_video_media_type(video_path):
    """Get the media type for a video file based on extension."""
    ext = Path(video_path).suffix.lower()
    media_types = {
        ".mp4": "video/mp4",
        ".avi": "video/x-msvideo",
        ".mov": "video/quicktime",
        ".mkv": "video/x-matroska",
        ".webm": "video/webm",
        ".flv": "video/x-flv",
        ".wmv": "video/x-ms-wmv",
        ".m4v": "video/mp4",
    }
    return media_types.get(ext, "video/mp4")


# ── §8i  TOOL DISPATCH ─────────────────────────────────────────

def execute_tool(name, args):
    """Execute a tool by name with given args dict. Returns string result."""
    try:
        if name == "bash":
            return _tool_bash(args.get("command", ""), args.get("timeout", 30))
        elif name == "read":
            return _tool_read(args.get("path", "."), args.get("offset", 1), args.get("limit", 2000))
        elif name == "write":
            return _tool_write(args.get("path", ""), args.get("content", ""))
        elif name == "edit":
            return _tool_edit(
                path=args.get("path", ""),
                new_string=args.get("new_string", ""),
                old_string=args.get("old_string", ""),
                strategy=args.get("strategy", "exact"),
                start_line=args.get("start_line"),
                end_line=args.get("end_line"),
                prefix=args.get("prefix", ""),
                suffix=args.get("suffix", ""),
                pattern=args.get("pattern", ""),
                replace_all=args.get("replace_all", False),
            )
        elif name == "glob":
            return _tool_glob(args.get("pattern", "*"), args.get("path", "."))
        elif name == "grep":
            return _tool_grep(
                pattern=args.get("pattern", ""),
                path=args.get("path", "."),
                include=args.get("include", "*"),
                literal=args.get("literal", False),
                case_insensitive=args.get("case_insensitive", False),
                max_results=args.get("max_results", 100),
            )
        elif name == "web_search":
            query = args.get("query", "")
            num_results = min(int(args.get("num_results", 5)), 10)
            if not query:
                return "Error: no search query specified"
            return _do_web_search(query, num_results)
        elif name == "task":
            # Task tool is handled directly in the main agent loop for parallel execution.
            # This path is only reached for sub-agents (which can't spawn further sub-agents).
            return "Sub-agents cannot spawn further sub-agents."
        elif name == "image_view":
            return _tool_image_view(args.get("image", ""), args.get("question", "Describe what you see in this image in detail."))
        elif name == "video_view":
            return _tool_video_view(args.get("video", ""), args.get("question", "Describe what happens in this video in detail."))
        elif name == "image_generate":
            return _tool_image_generate(args.get("prompt", ""), args.get("size", "1024x1024"), args.get("output", ""))
        elif name == "video_generate":
            return _tool_video_generate(args.get("prompt", ""), args.get("output", ""))
        elif name == "mkdir":
            return _tool_mkdir(args.get("path", ""))
        elif name == "rm":
            return _tool_rm(args.get("path", ""))
        elif name == "git":
            return _tool_git(args.get("args", ""))
        elif name == "sysinfo":
            return _tool_sysinfo()
        elif name == "tree":
            return _tool_tree(args.get("path", "."), args.get("depth", 3))
        elif name == "cd":
            return _tool_cd(args.get("path", ""))
        elif name == "pwd":
            return _tool_pwd()
        # Legacy aliases
        elif name == "ls":
            return _tool_read(args.get("path", "."))
        elif name == "cat":
            return _tool_read(args.get("path", ""))
        elif name in ("head", "tail"):
            path = args.get("path", "")
            n = args.get("n", 10)
            offset = 1 if name == "head" else None
            if name == "head":
                return _tool_read(path, offset=1, limit=n)
            else:
                # Read all, return last N lines
                result = _tool_read(path)
                lines = result.split('\n')
                return '\n'.join(lines[-n:])
        elif name == "curl":
            url = args.get("url", "")
            try:
                result = subprocess.run(
                    ["curl", "-sL", "--max-time", "15", url],
                    capture_output=True, text=True, timeout=20,
                    encoding='utf-8', errors='replace'
                )
                content = result.stdout
                if len(content) > 5000:
                    content = content[:5000] + "\n... (truncated)"
                return content or "(empty response)"
            except Exception as e:
                return f"curl error: {e}"
        elif name in ("cp", "mv"):
            # These aren't in TOOL_DEFINITIONS but handle text-parsed calls
            return _tool_bash(f"{name} {args.get('src', '')} {args.get('dst', '')}")
        elif name == "find":
            name_pattern = args.get("name", "*")
            search_path = args.get("path", ".")
            return _tool_glob(f"**/{name_pattern}", search_path)
        else:
            return f"Unknown tool: {name}"

    except Exception as e:
        return f"Tool error ({name}): {traceback.format_exc()}"

# ════════════════════════════════════════════════════════════════
# §9  LLM CLIENT
#     Direct API with 429 retry, rate limiting, ESC interrupt
# ════════════════════════════════════════════════════════════════

def _build_messages_for_api(messages, system_prompt=None, strip_old_tools=True):
    """Build OpenAI-compatible messages list, preserving native tool_calls format.
    When strip_old_tools=True (default), tool call results from rounds older than
    the last 10 are omitted to save tokens. The last 10 rounds keep everything intact."""
    # Apply tool result stripping outside the recent token budget to save tokens
    if strip_old_tools and len(messages) > 10:
        processed = _strip_old_tool_calls(messages, keep_tokens=KEEP_RECENT_TOKENS)
    else:
        processed = messages

    api_messages = []
    if system_prompt:
        api_messages.append({"role": "system", "content": system_prompt})
    for msg in processed:
        if "_raw_api_msg" in msg:
            api_messages.append(msg["_raw_api_msg"])
        elif msg.get("role") == "tool":
            api_messages.append({"role": "tool", "content": msg["content"], "tool_call_id": msg.get("tool_call_id", "auto")})
        else:
            api_messages.append({"role": msg["role"], "content": msg["content"]})
    return api_messages

def _classify_429_error(err_body):
    """Classify 429 error as 'peak', 'limit', or 'unknown'."""
    body_lower = err_body.lower() if err_body else ""
    limit_keywords = [
        "rate limit", "rate_limit", "too many requests", "quota exceeded",
        "请求过于频繁", "并发", "qps", "请求次数", "调用频繁",
        "access too frequent", "too frequently", "request frequency",
        "limit exceeded", "throttl",
    ]
    peak_keywords = [
        "peak", "busy", "overloaded", "capacity", "峰值", "繁忙", "服务忙",
        "server is busy", "temporarily unavailable", "try again later",
        "server load", "high traffic", "流量", "拥堵", "拥挤",
        "capacity exceeded", "资源不足",
    ]
    for kw in limit_keywords:
        if kw in body_lower:
            return "limit"
    for kw in peak_keywords:
        if kw in body_lower:
            return "peak"
    return "unknown"

def _interruptible_sleep(seconds):
    """Sleep that can be interrupted by ESC stop flag."""
    for _ in range(int(seconds * 10)):
        if _check_esc_stop():
            return True
        time.sleep(0.1)
    return False

def _make_api_request(url, headers, body, max_retries=10):
    """Make API request with 429 infinite retry and rate limiting."""
    import urllib.request
    import urllib.error

    retry_429_count = 0
    other_retry_count = 0

    while True:
        if _check_esc_stop():
            return None, "ESC_STOP"

        rate_limiter.wait(silent=True)
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data, None

        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            if e.code == 429:
                retry_429_count += 1
                reason = _classify_429_error(err_body)
                if reason == "limit":
                    wait = 30
                    msg = t("retry.429.limit", n=retry_429_count, wait=wait)
                elif reason == "peak":
                    wait = 3
                    msg = t("retry.429.peak", n=retry_429_count, wait=wait)
                else:
                    wait = 5
                    msg = t("retry.429.unknown", n=retry_429_count, wait=wait)
                console.print(f"[yellow]⚠️ {msg}[/yellow]")
                if _interruptible_sleep(wait):
                    return None, "ESC_STOP"
                continue
            return None, f"API HTTP {e.code}: {err_body[:300]}"

        except urllib.error.URLError as e:
            other_retry_count += 1
            if other_retry_count <= max_retries:
                wait = 3
                console.print(f"[yellow]⚠️ {t('retry.conn_fail', n=other_retry_count, wait=wait, reason=e.reason)}[/yellow]")
                if _interruptible_sleep(wait):
                    return None, "ESC_STOP"
                continue
            return None, f"API connection error: {e.reason}"

        except Exception as e:
            return None, f"API call error: {e}"

    return None, t("retry.max")

def call_llm_direct(messages, system_prompt=None, model=None, use_tools=True, is_subagent=False, thinking_log_path=None):
    """Call LLM via direct HTTP API with SSE streaming and native function calling.
    Returns (text, tool_calls, error, already_displayed).
    - already_displayed: True if the text was already printed via streaming Panel.
    tool_calls is a list of {"name": ..., "args": ..., "id": ...} or None.

    Features merged from multiple versions:
    - SSE streaming with Rich Live (from 26w03a)
    - Unlimited 429 retry with ESC-interruptible sleep (from 1.6.1)
    - Enhanced 429 error classification (from 1.6.1)
    - Thinking mode support with reasoning_content (from 26w15a)
    - Sub-agent support (from 26w15a)
    - Multi-model fallback with backup models (from 26w28a)
    """
    config = load_zai_config()
    if not config:
        return None, None, t("llm.no_config"), False

    # Use load_full_config to get multi-model settings
    full_config = load_full_config()

    default_base = config.get("baseUrl", "").rstrip("/")
    default_key = config.get("apiKey", "")

    # Determine which model to use (with fallback logic)
    if model:
        # Explicit model override (e.g. from sub-agent)
        active_model = model
        # Safety check: image/video gen models don't support chat/completions endpoint
        caps = get_model_capabilities(active_model)
        if ("image_gen" in caps or "video_gen" in caps) and "text" not in caps:
            return None, None, f"Model {active_model} does not support chat completions (use image_generate/video_generate tool instead)", False
    elif not is_subagent:
        # Main agent: check fallback state
        use_backup, active_model = model_fallback.should_use_backup_main(full_config)
        if use_backup:
            primary = full_config.get("model", "glm-4-flash")
            console.print(f"[yellow]{t('model.switch_back', model=primary)}[/yellow]")
    else:
        active_model = config.get("model", "glm-4-flash")

    # Resolve endpoint through multi-key routing:
    # modelKeys[model] > providerKeys[provider] > default baseUrl/apiKey.
    # Any custom model name works as long as some key covers it.
    base_url, api_key = resolve_model_endpoint(active_model, full_config)
    if not base_url:
        base_url = default_base
    if not api_key:
        api_key = default_key
    if not base_url or not api_key:
        return None, None, t("llm.invalid_config"), False

    def _apply_endpoint_for(mdl):
        """Re-resolve base_url/api_key after a backup-model switch."""
        nonlocal base_url, api_key, url
        base_url, api_key = resolve_model_endpoint(mdl, full_config)
        if not base_url:
            base_url = default_base
        if not api_key:
            api_key = default_key
        url = f"{base_url}/chat/completions"
        # headers were built before the loop; keep Authorization in sync or
        # every request after a switch still carries the OLD model's key (401)
        headers["Authorization"] = f"Bearer {api_key}"

    url = f"{base_url}/chat/completions"
    api_messages = _build_messages_for_api(messages, system_prompt)

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    if config.get("chatId"):
        headers["X-Chat-Id"] = config["chatId"]
    if config.get("userId"):
        headers["X-User-Id"] = config["userId"]
    if config.get("token"):
        headers["X-Token"] = config["token"]

    body = {
        "model": active_model,
        "messages": api_messages,
        "stream": True,  # SSE streaming enabled
    }

    # Add tools if enabled
    # Main agent: CAN use task (to delegate to sub-agents), but CANNOT use image_generate/video_generate (sub-agent only)
    # Sub-agents: CANNOT use task (prevent recursion), but CAN use image_generate/video_generate
    if use_tools and TOOL_DEFINITIONS:
        if is_subagent:
            # Sub-agents cannot use the task tool (prevent recursion), but CAN use image/video gen
            filtered_tools = [t for t in TOOL_DEFINITIONS if t.get("function", {}).get("name") != "task"]
            body["tools"] = filtered_tools
        else:
            # Main agent: CAN use task (for delegation), CANNOT use image_generate/video_generate directly
            _main_excluded = {"image_generate", "video_generate"}
            filtered_tools = [t for t in TOOL_DEFINITIONS if t.get("function", {}).get("name") not in _main_excluded]
            body["tools"] = filtered_tools
        body["tool_choice"] = "auto"

    # Thinking mode: add system instruction and model parameters
    thinking_mode = get_thinking_mode()
    thinking_start_time = None
    if thinking_mode == "on":
        thinking_start_time = time.time()
        # Add thinking instruction to the last system message
        for msg in api_messages:
            if msg.get("role") == "system":
                msg["content"] += "\n\nYou must always show your detailed thinking process before answering."
                break
        # For models that support reasoning_effort, set to maximum
        # Only set for OpenAI and DeepSeek models that support this parameter
        # GLM models (including z1) do NOT support reasoning_effort
        _model_lower = active_model.lower()
        if _model_lower.startswith("o3-") or "deepseek-reasoner" in _model_lower:
            body["reasoning_effort"] = "high"

    # Create thinking log file
    global _current_thinking_log
    _thinking_log_path = thinking_log_path or THINKING_LOG_DIR / f"thinking_{int(time.time()*1000)}.log"
    _current_thinking_log = _thinking_log_path
    _thinking_has_content = False
    # Eagerly create the log file so the clickable launcher works immediately
    # (before the first reasoning delta arrives, e.g. during 429 retries).
    try:
        _thinking_log_file = open(_thinking_log_path, "w", encoding="utf-8")
        _thinking_log_file.write(f"💭 思考过程 - {datetime.now().isoformat()}\n")
        _thinking_log_file.write("=" * 50 + "\n\n")
        _thinking_log_file.flush()
        _thinking_has_content = True
    except Exception:
        _thinking_log_file = None

    def _thinking_log_write(text):
        """Append text to the thinking log (opens the file in append mode if
        needed). Safe to call from retry handlers; never raises."""
        nonlocal _thinking_log_file
        try:
            if _thinking_log_file is None:
                _thinking_log_file = open(_thinking_log_path, "a", encoding="utf-8")
            _thinking_log_file.write(text)
            _thinking_log_file.flush()
        except Exception:
            _thinking_log_file = None

    def _close_thinking_log():
        """Close the thinking log handle if open. Called on every early return
        (errors/ESC) — leaking the handle keeps the file locked on Windows."""
        nonlocal _thinking_log_file
        try:
            if _thinking_log_file:
                _thinking_log_file.close()
        except Exception:
            pass
        _thinking_log_file = None

    # Reset sub-agent thinking flag
    if is_subagent:
        call_llm_direct._subagent_thinking_started = False

    # --- SSE Streaming with Rich Live ---
    import urllib.request
    import urllib.error

    retry_429_count = 0  # Unlimited retries for 429
    other_retry_count = 0  # Limited retries for other errors
    max_other_retries = 10
    _reset_thinking_force_stop()
    # Pre-bind so exception handlers can always build stop metadata
    full_text = ""
    reasoning_content = ""
    usage_data = None

    def _stop_meta():
        _rt = 0
        if isinstance(usage_data, dict):
            _d = usage_data.get("completion_tokens_details") or {}
            _rt = int(_d.get("reasoning_tokens") or usage_data.get("reasoning_tokens") or 0)
        if not _rt:
            _rt = _estimate_tokens(reasoning_content)
        return {"reasoning": reasoning_content, "reasoning_tokens": _rt, "usage": usage_data}

    while True:
        # Check ESC stop before each request
        if _check_esc_stop():
            if _thinking_log_file:
                try:
                    _thinking_log_file.close()
                except Exception:
                    pass
                _thinking_log_file = None
            return None, None, "ESC_STOP", False

        # For sub-agents: write rate limit wait messages to sub-agent's log, not main console
        _subagent_log_path = str(_thinking_log_path) if is_subagent else None
        rate_limiter.wait(silent=is_subagent and not _subagent_log_path, log_path=_subagent_log_path)
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=120)
            # Register so ESC can kill this socket instantly from the listener thread
            _register_response(resp)

            # Parse SSE stream
            full_text = ""
            reasoning_content = ""  # For thinking mode (DeepSeek-R1 style)
            tool_calls_agg = {}
            tool_calls_order = []
            usage_data = None
            stream_stop_reason = None  # None | "esc" | "force"

            # Streaming display with Rich Live
            # For sub-agents: suppress ALL console output (goes to log file only)
            # For main agent: transient Live streams THINKING first, then the answer
            from rich.live import Live
            live_text = ""
            last_live_update = 0.0
            LIVE_UPDATE_INTERVAL = 0.08
            THINKING_PREVIEW_CHARS = 4000

            def _render_stream():
                """Live renderable: streaming reasoning (dim italic panel) while the
                model thinks, switching to plain answer text once content arrives."""
                if reasoning_content and not live_text:
                    preview = reasoning_content[-THINKING_PREVIEW_CHARS:]
                    rtok = _estimate_tokens(reasoning_content)
                    _zh = _get_lang() == "zh"
                    title = (f"💭 思考流式输出中... ~{rtok} tokens" if _zh
                             else f"💭 Thinking... ~{rtok} tokens")
                    return Panel(
                        Text(preview, style="italic grey58", overflow="fold"),
                        title=title, title_align="left",
                        border_style="grey42", padding=(0, 1),
                    )
                return Text(live_text)

            # Quiet mode: no Live thinking stream — the single work status line owns
            # the display; thinking still streams into the log file.
            _live_on = not is_subagent and not is_quiet_tools_enabled()
            live = None
            if _live_on:
                live = Live(
                    console=console,
                    refresh_per_second=12,
                    transient=True,
                )
                live.start()
            # Quiet mode: expose the status line for live activity (thinking/content)
            _quiet_ws = _CURRENT_WORK_STATUS if (not is_subagent and is_quiet_tools_enabled()) else None

            try:
                for raw_line in resp:
                    # Immediate stop checks: ESC (user) or single-round thinking limit.
                    # The ESC listener thread also closes this socket from its side,
                    # unblocking this iterator even while waiting for the next chunk.
                    if _check_esc_stop():
                        stream_stop_reason = "esc"
                        break
                    if _check_thinking_force_stop():
                        stream_stop_reason = "force"
                        break

                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue

                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    # Capture token usage BEFORE the empty-choices check:
                    # the final stream chunk often carries usage with choices=[]
                    _usage_chunk = chunk.get("usage")
                    if _usage_chunk:
                        usage_data = _usage_chunk

                    choices = chunk.get("choices", [])
                    if not choices:
                        continue

                    delta = choices[0].get("delta", {})

                    # Handle reasoning_content (DeepSeek-R1 style thinking)
                    rc = delta.get("reasoning_content", None)
                    if rc is not None and rc:
                        reasoning_content += rc
                        if is_subagent:
                            # For sub-agents: write thinking directly to the log path
                            # passed via thinking_log_path parameter (avoids race condition
                            # with multiple sub-agents when searching _active_subagents)
                            _subagent_thinking_started = getattr(call_llm_direct, '_subagent_thinking_started', False)
                            if not _subagent_thinking_started:
                                try:
                                    with open(_thinking_log_path, "a", encoding="utf-8") as _sa_logf:
                                        _sa_logf.write(f"\n[Thinking]:\n")
                                        _sa_logf.write(f"💭 思考过程 - {datetime.now().isoformat()}\n")
                                        _sa_logf.write("=" * 50 + "\n\n")
                                    call_llm_direct._subagent_thinking_started = True
                                except Exception:
                                    pass
                            try:
                                with open(_thinking_log_path, "a", encoding="utf-8") as _sa_logf:
                                    _sa_logf.write(rc)
                                    _sa_logf.flush()
                            except Exception:
                                pass
                        else:
                            # For main agent: append to the eagerly-created log file
                            if not _thinking_log_file:
                                try:
                                    _thinking_log_file = open(_thinking_log_path, "a", encoding="utf-8")
                                    _thinking_has_content = True
                                except Exception:
                                    _thinking_log_file = None
                            _thinking_log_write(rc)
                            # Stream the thinking itself in the Live window
                            now = time.time()
                            if _live_on and now - last_live_update >= LIVE_UPDATE_INTERVAL:
                                try:
                                    live.update(_render_stream())
                                    last_live_update = now
                                except Exception:
                                    pass
                            # Quiet mode: live thinking progress on the status line
                            if _quiet_ws is not None and now - getattr(_quiet_ws, "_last_act", 0) >= 0.4:
                                _quiet_ws._last_act = now
                                _quiet_ws.set_activity(f"💭 " + ("思考中" if _get_lang() == "zh" else "thinking") + f" ~{_estimate_tokens(reasoning_content)} tok")
                            # Independent per-round thinking counter: hard-stop at the
                            # single-round token limit (main agent only; it retries after
                            # a synchronous thought compression in the agent loop).
                            if not is_subagent and _estimate_tokens(reasoning_content) >= get_thinking_single_round_force_tokens():
                                stream_stop_reason = "force"
                                _trigger_thinking_force_stop()
                                break

                    # Handle text content — stream with throttled Live updates
                    content = delta.get("content", None)
                    if content is not None:
                        if content:
                            full_text += content
                            if not is_subagent:
                                live_text += content
                                now = time.time()
                                if _live_on and now - last_live_update >= LIVE_UPDATE_INTERVAL:
                                    live.update(_render_stream())
                                    last_live_update = now
                                if _quiet_ws is not None and now - getattr(_quiet_ws, "_last_act", 0) >= 0.4:
                                    _quiet_ws._last_act = now
                                    _quiet_ws.set_activity("✍️ " + ("回答中" if _get_lang() == "zh" else "answering") + f" ~{len(full_text)} 字")

                    # Handle tool calls (streaming)
                    tc_deltas = delta.get("tool_calls", None)
                    if tc_deltas:
                        for tc_delta in tc_deltas:
                            tc_id = tc_delta.get("id", "")
                            tc_index = tc_delta.get("index", 0)
                            if tc_id:
                                fn = tc_delta.get("function", {})
                                arg_chunk = fn.get("arguments", "")
                                if tc_index in tool_calls_agg:
                                    # Compatible providers resend the id on every
                                    # chunk: treat as continuation instead of
                                    # overwriting accumulated arguments
                                    if fn.get("name"):
                                        tool_calls_agg[tc_index]["name"] = fn["name"]
                                    if arg_chunk:
                                        tool_calls_agg[tc_index]["args_str"] += arg_chunk
                                else:
                                    tool_calls_agg[tc_index] = {
                                        "name": fn.get("name", ""),
                                        "args_str": arg_chunk,
                                        "id": tc_id,
                                    }
                                    tool_calls_order.append(tc_index)
                            elif tc_index in tool_calls_agg:
                                fn = tc_delta.get("function", {})
                                arg_chunk = fn.get("arguments", "")
                                if arg_chunk:
                                    tool_calls_agg[tc_index]["args_str"] += arg_chunk

            finally:
                # Unregister and close the SSE socket (unblocks ESC-killed streams)
                try:
                    _unregister_response(resp)
                    resp.close()
                except Exception:
                    pass
                # Final streaming update then stop (transient=True erases it)
                if _live_on:
                    if live_text.strip():
                        live.update(Text(live_text))
                        time.sleep(0.03)
                    live.stop()
                # Close thinking log file
                if _thinking_log_file:
                    _done_label = "思考被中断" if stream_stop_reason else "思考完成"
                    _thinking_log_file.write("\n\n" + "=" * 50 + "\n")
                    _thinking_log_file.write(f"{_done_label} - {datetime.now().isoformat()}\n")
                    _thinking_log_file.close()
                    _thinking_log_file = None
                # For sub-agents: write thinking completion marker to sub-agent log
                if is_subagent and reasoning_content.strip():
                    try:
                        with open(_thinking_log_path, "a", encoding="utf-8") as _sa_logf:
                            _sa_logf.write("\n\n" + "=" * 50 + "\n")
                            _sa_logf.write(f"思考完成 - {datetime.now().isoformat()}\n")
                        call_llm_direct._subagent_thinking_started = False
                    except Exception:
                        pass

            # Real reasoning token usage when the provider reports it, else estimate
            reasoning_tokens = 0
            if isinstance(usage_data, dict):
                _details = usage_data.get("completion_tokens_details") or {}
                reasoning_tokens = int(
                    _details.get("reasoning_tokens")
                    or usage_data.get("reasoning_tokens")
                    or 0
                )
            if not reasoning_tokens:
                reasoning_tokens = _estimate_tokens(reasoning_content)
            meta = {
                "reasoning": reasoning_content,
                "reasoning_tokens": reasoning_tokens,
                "usage": usage_data,
            }

            # Immediate stop outcomes (ESC or single-round thinking hard limit)
            if stream_stop_reason == "esc":
                return None, None, "ESC_STOP", False, meta
            if stream_stop_reason == "force":
                return None, None, "THINKING_FORCE_STOP", False, meta

            # Display thinking content — ONLY for main agent, and only as a clickable link
            # Quiet mode (hideWorkOutput): stay silent — the work status line covers it,
            # thinking still goes to the log file.
            if reasoning_content.strip() and not is_subagent and not is_quiet_tools_enabled():
                thinking_elapsed = time.time() - thinking_start_time if thinking_start_time else 0.0
                # Create launcher script and show clickable link
                launcher_path = _create_log_launcher(_thinking_log_path, title=f"💭 {t('thinking.process', time=f'{thinking_elapsed:.1f}')}")
                link = _make_clickable_link(launcher_path, label=f"💭 {t('thinking.process', time=f'{thinking_elapsed:.1f}')} - 📄 Ctrl+点击查看")
                console.print(f"  {link}")

            # Final display: print the complete response in a nice Panel — ONLY for main agent
            # Quiet mode: agent_loop decides what to show (intermediate text stays hidden,
            # only the final no-tool-call round is rendered there).
            if full_text.strip() and not is_subagent and not is_quiet_tools_enabled():
                try:
                    console.print(Panel(Markdown(full_text.strip()), title=f"🤖 {APP_NAME}", border_style="green", padding=(1, 2)))
                except Exception:
                    console.print(Panel(full_text.strip(), title=f"🤖 {APP_NAME}", border_style="green", padding=(1, 2)))

            # Parse tool calls from aggregated SSE deltas
            parsed_tool_calls = []
            for idx in tool_calls_order:
                if idx in tool_calls_agg:
                    tc = tool_calls_agg[idx]
                    try:
                        args = json.loads(tc["args_str"]) if tc["args_str"] else {}
                    except json.JSONDecodeError:
                        args = {"_raw": tc["args_str"]}
                    parsed_tool_calls.append({
                        "name": tc["name"],
                        "args": args,
                        "id": tc["id"],
                    })

            # Also check text for tool call patterns (fallback)
            text_tool_calls = parse_tool_calls(full_text) if full_text else []

            all_tool_calls = parsed_tool_calls if parsed_tool_calls else []
            if text_tool_calls and not all_tool_calls:
                all_tool_calls = text_tool_calls

            clean_text = full_text
            if all_tool_calls and not parsed_tool_calls:
                for pattern in TOOL_CALL_PATTERNS:
                    clean_text = pattern.sub("", clean_text)
                clean_text = re.sub(r'```[\s\S]*?```', lambda m: _strip_tool_calls_from_codeblock(m.group()), clean_text)
                clean_text = clean_text.strip()

            # Report success to fallback state tracker
            if not is_subagent:
                model_fallback.report_main_success()

            return clean_text if clean_text else full_text.strip(), all_tool_calls if all_tool_calls else None, None, True, meta

        except urllib.error.HTTPError as e:
            # If the socket was killed intentionally, honor the stop immediately
            if _check_thinking_force_stop():
                _close_thinking_log()
                return None, None, "THINKING_FORCE_STOP", False, _stop_meta()
            if _check_esc_stop():
                _close_thinking_log()
                return None, None, "ESC_STOP", False, _stop_meta()
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass

            if e.code == 429:
                # 429 errors: try backup model if available, otherwise retry
                retry_429_count += 1
                reason = _classify_429_error(err_body)
                if reason == "limit":
                    wait = 30
                    msg = t("retry.429.limit", n=retry_429_count, wait=wait)
                elif reason == "peak":
                    wait = 3
                    msg = t("retry.429.peak", n=retry_429_count, wait=wait)
                else:
                    wait = 5
                    msg = t("retry.429.unknown", n=retry_429_count, wait=wait)
                if is_subagent:
                    # For sub-agents: write 429 retry info to sub-agent log, not main console
                    _thinking_log_write(f"\n⚠️ {msg}\n")
                else:
                    # Main agent: mirror the retry warning into the thinking log so
                    # the clickable log also shows what happened during retries
                    _thinking_log_write(f"\n⚠️ {msg}\n")
                    backup = full_config.get("backupModel", "")
                    should_switch, new_model = model_fallback.report_main_error(full_config, is_429=True)
                    if should_switch:
                        if not _quiet_log_msg(t('model.switch_backup', model=active_model, backup=backup)):
                            console.print(f"[yellow]{t('model.switch_backup', model=active_model, backup=backup)}[/yellow]")
                        active_model = new_model
                        body["model"] = active_model
                        # Update base_url for backup model if different provider
                        _apply_endpoint_for(active_model)
                        # Brief cooldown before hitting the backup endpoint — without
                        # this a failing backup turns into a no-sleep hot loop
                        if _interruptible_sleep(3):
                            _close_thinking_log()
                            return None, None, "ESC_STOP", False
                        continue
                    else:
                        if not _quiet_log_msg(msg):
                            console.print(f"[yellow]⚠️ {msg}[/yellow]")
                        if backup:
                            # Backup is configured but not yet activated (retrying before switching)
                            left = model_fallback.MAX_429_RETRIES - retry_429_count
                            if not _quiet_log_msg(t('model.retry_before_backup', model=active_model, count=retry_429_count, max=model_fallback.MAX_429_RETRIES, left=left)):
                                console.print(f"[dim]{t('model.retry_before_backup', model=active_model, count=retry_429_count, max=model_fallback.MAX_429_RETRIES, left=left)}[/dim]")
                        else:
                            # No backup model configured at all
                            if not _quiet_log_msg(t('model.no_backup_retry', model=active_model)):
                                console.print(f"[dim]{t('model.no_backup_retry', model=active_model)}[/dim]")
                if _interruptible_sleep(wait):
                    _close_thinking_log()
                    return None, None, "ESC_STOP", False
                continue

            # Non-429 HTTP errors: try backup after consecutive failures
            if not is_subagent:
                should_switch, new_model = model_fallback.report_main_error(full_config, is_429=False)
                backup = full_config.get("backupModel", "")
                if should_switch and backup:
                    if not _quiet_log_msg(t('model.fallback_non429', model=active_model, count=model_fallback._main_consecutive_errors) + " / " + t('model.switch_backup', model=active_model, backup=backup)):
                        console.print(f"[yellow]{t('model.fallback_non429', model=active_model, count=model_fallback._main_consecutive_errors)}[/yellow]")
                        console.print(f"[yellow]{t('model.switch_backup', model=active_model, backup=backup)}[/yellow]")
                    active_model = new_model
                    body["model"] = active_model
                    _apply_endpoint_for(active_model)
                    if _interruptible_sleep(3):
                        _close_thinking_log()
                        return None, None, "ESC_STOP", False
                    continue
                elif backup:
                    # Still have retries left, try again with primary
                    other_retry_count += 1
                    if other_retry_count <= max_other_retries:
                        wait = 3
                        if not _quiet_log_msg(f"API HTTP {e.code}: {err_body[:200]}"):
                            console.print(f"[yellow]⚠️ API HTTP {e.code}: {err_body[:200]}[/yellow]")
                        if _interruptible_sleep(wait):
                            _close_thinking_log()
                            return None, None, "ESC_STOP", False
                        continue
                    # Exhausted retries on primary, force backup state so the
                    # next round doesn't flip straight back to the primary model
                    model_fallback.force_use_backup_main(full_config)
                    if not _quiet_log_msg(t('model.switch_backup', model=active_model, backup=backup)):
                        console.print(f"[yellow]{t('model.switch_backup', model=active_model, backup=backup)}[/yellow]")
                    active_model = backup
                    body["model"] = active_model
                    _apply_endpoint_for(active_model)
                    if _interruptible_sleep(3):
                        _close_thinking_log()
                        return None, None, "ESC_STOP", False
                    continue
            _close_thinking_log()
            return None, None, f"API HTTP {e.code}: {err_body[:300]}", False

        except urllib.error.URLError as e:
            # Killed by ESC or the thinking hard limit mid-stream: stop right away
            if _check_thinking_force_stop():
                _close_thinking_log()
                return None, None, "THINKING_FORCE_STOP", False, _stop_meta()
            if _check_esc_stop():
                _close_thinking_log()
                return None, None, "ESC_STOP", False, _stop_meta()
            other_retry_count += 1
            if other_retry_count <= max_other_retries:
                wait = 3
                retry_msg = t('retry.conn_fail', n=other_retry_count, wait=wait, reason=e.reason)
                # Mirror connection-failure warnings into the thinking log
                _thinking_log_write(f"\n⚠️ {retry_msg}\n")
                if not is_subagent:
                    # Try backup model for connection errors too
                    should_switch, new_model = model_fallback.report_main_error(full_config, is_429=False)
                    backup = full_config.get("backupModel", "")
                    if should_switch and backup:
                        if not _quiet_log_msg(t('model.fallback_non429', model=active_model, count=model_fallback._main_consecutive_errors) + " / " + t('model.switch_backup', model=active_model, backup=backup)):
                            console.print(f"[yellow]{t('model.fallback_non429', model=active_model, count=model_fallback._main_consecutive_errors)}[/yellow]")
                        active_model = new_model
                        body["model"] = active_model
                        _apply_endpoint_for(active_model)
                        if _interruptible_sleep(3):
                            _close_thinking_log()
                            return None, None, "ESC_STOP", False
                        continue
                    if not _quiet_log_msg(retry_msg):
                        console.print(f"[yellow]⚠️ {retry_msg}[/yellow]")
                if _interruptible_sleep(wait):
                    _close_thinking_log()
                    return None, None, "ESC_STOP", False
                continue
            # Exhausted retries: try backup if available
            if not is_subagent:
                backup = full_config.get("backupModel", "")
                if backup:
                    model_fallback.force_use_backup_main(full_config)
                    if not _quiet_log_msg(t('model.switch_backup', model=active_model, backup=backup)):
                        console.print(f"[yellow]{t('model.switch_backup', model=active_model, backup=backup)}[/yellow]")
                    active_model = backup
                    body["model"] = active_model
                    _apply_endpoint_for(active_model)
                    if _interruptible_sleep(3):
                        _close_thinking_log()
                        return None, None, "ESC_STOP", False
                    continue
            _close_thinking_log()
            return None, None, f"API connection error: {e.reason}", False

        except Exception as e:
            # Killed by ESC or the thinking hard limit mid-stream: stop right away
            if _check_thinking_force_stop():
                _close_thinking_log()
                return None, None, "THINKING_FORCE_STOP", False, _stop_meta()
            if _check_esc_stop():
                _close_thinking_log()
                return None, None, "ESC_STOP", False, _stop_meta()
            if not is_subagent:
                backup = full_config.get("backupModel", "")
                if backup:
                    should_switch, new_model = model_fallback.report_main_error(full_config, is_429=False)
                    if should_switch:
                        if not _quiet_log_msg(t('model.switch_backup', model=active_model, backup=backup)):
                            console.print(f"[yellow]{t('model.switch_backup', model=active_model, backup=backup)}[/yellow]")
                        active_model = new_model
                        body["model"] = active_model
                        _apply_endpoint_for(active_model)
                        if _interruptible_sleep(3):
                            _close_thinking_log()
                            return None, None, "ESC_STOP", False
                        continue
            _close_thinking_log()
            return None, None, f"API call error: {e}", False

    _close_thinking_log()
    return None, None, t("retry.max"), False

# ════════════════════════════════════════════════════════════════
# §10 TOOL CALL PARSING (text-based fallback)
# ════════════════════════════════════════════════════════════════

TOOL_CALL_PATTERNS = [
    re.compile(r'🔔\s*TOOL_CALL:\s*(\w+)\s*(\{[^}]*(?:\{[^}]*\}[^}]*)*\})', re.DOTALL),
    re.compile(r'TOOL_CALL:\s*(\w+)\s*(\{[^}]*(?:\{[^}]*\}[^}]*)*\})', re.DOTALL),
    re.compile(r'🔔\s*TOOL_CALL:\s*(\w+)\s*\(([^)]*)\)'),
    re.compile(r'TOOL_CALL:\s*(\w+)\s*\(([^)]*)\)'),
    re.compile(r'<\s*tool_call:\s*(\w+)\s+(\{[^>]*\})\s*>', re.DOTALL),
    re.compile(r'\[\s*TOOL_CALL:\s*(\w+)\s+(\{[^\]]*\})\s*\]', re.DOTALL),
]

_TOOL_CALL_NAME_PATTERNS = [
    re.compile(r'(?:🔔\s*)?TOOL_CALL:\s*(\w+)\s*\{'),
    re.compile(r'<\s*tool_call:\s*(\w+)\s+\{'),
    re.compile(r'\[\s*TOOL_CALL:\s*(\w+)\s+\{'),
]

def _extract_json_from_position(text, start_pos):
    if start_pos >= len(text) or text[start_pos] != '{':
        return None, start_pos
    depth = 0
    in_string = False
    escape = False
    i = start_pos
    while i < len(text):
        ch = text[i]
        if escape:
            escape = False
            i += 1
            continue
        if ch == '\\' and in_string:
            escape = True
            i += 1
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            i += 1
            continue
        if in_string:
            i += 1
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                json_str = text[start_pos:i+1]
                try:
                    parsed = json.loads(json_str)
                    return parsed, i + 1
                except json.JSONDecodeError:
                    return None, i + 1
        i += 1
    return None, i

def _strip_tool_calls_from_codeblock(text):
    for pattern in TOOL_CALL_PATTERNS:
        text = pattern.sub("", text)
    return text

def parse_tool_calls(text):
    if not text:
        return []
    clean_text = text.replace("```", "")
    calls = []
    seen = set()
    for pattern in TOOL_CALL_PATTERNS:
        for match in pattern.finditer(clean_text):
            tool_name = match.group(1)
            args_str = match.group(2)
            key = f"{tool_name}:{args_str}"
            if key in seen:
                continue
            seen.add(key)
            try:
                args = json.loads(args_str)
            except json.JSONDecodeError:
                args = {"_raw": args_str}
            calls.append({"name": tool_name, "args": args, "id": ""})

    if not calls:
        for name_pattern in _TOOL_CALL_NAME_PATTERNS:
            for match in name_pattern.finditer(clean_text):
                tool_name = match.group(1)
                brace_pos = match.end() - 1
                if brace_pos < len(clean_text) and clean_text[brace_pos] == '{':
                    parsed, end_pos = _extract_json_from_position(clean_text, brace_pos)
                    if parsed:
                        key = f"{tool_name}:{json.dumps(parsed, sort_keys=True)}"
                        if key not in seen:
                            seen.add(key)
                            calls.append({"name": tool_name, "args": parsed, "id": ""})
    return calls

# ════════════════════════════════════════════════════════════════
# §11 CONVERSATION MANAGER
# ════════════════════════════════════════════════════════════════

class Conversation:
    def __init__(self):
        self.messages = []
        self.id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.title = "New Conversation"
        self.created = datetime.now().isoformat()
        self.system_prompt = get_system_prompt()
        # ── Chain-of-thought retention state (26w35a+) ──
        self.thinking_summary = ""     # accumulated compressed reasoning
        # archive entries: {"text": str, "tokens": int, "compressing": bool}
        self.thinking_archive = []
        self._think_lock = threading.RLock()
        self._think_bg_task = None     # running ThinkingCompressTask (transient)

    def add(self, role, content, **kwargs):
        msg = {"role": role, "content": content, "time": datetime.now().isoformat()}
        msg.update(kwargs)
        self.messages.append(msg)

    def save(self):
        data = {
            "id": self.id,
            "title": self.title,
            "created": self.created,
            "system_prompt": self.system_prompt,
            "thinking_summary": self.thinking_summary,
            "thinking_archive": [
                {"text": e.get("text", ""), "tokens": e.get("tokens", 0)}
                for e in self.thinking_archive
            ],
            "messages": [],
        }
        for msg in self.messages:
            m = dict(msg)  # Keep all fields including _raw_api_msg
            data["messages"].append(m)
        path = CONV_DIR / f"{self.id}.json"
        _atomic_write_json(path, data, indent=2, default=str)

    @classmethod
    def load(cls, path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        conv = cls.__new__(cls)
        conv.id = data.get("id", "unknown")
        conv.title = data.get("title", "New Conversation")
        conv.created = data.get("created", "")
        conv.system_prompt = data.get("system_prompt", SYSTEM_PROMPT_DEFAULT)
        conv.messages = data.get("messages", [])
        # Backward-compatible defaults for pre-26w35a conversations
        conv.thinking_summary = data.get("thinking_summary", "") or ""
        conv.thinking_archive = []
        for e in (data.get("thinking_archive") or []):
            if isinstance(e, dict) and e.get("text"):
                conv.thinking_archive.append({
                    "text": e.get("text", ""),
                    "tokens": int(e.get("tokens", 0) or 0),
                    "compressing": False,  # any in-flight flag died with the process
                })
        conv._think_lock = threading.RLock()
        conv._think_bg_task = None
        return conv

    def thinking_pending_tokens(self):
        """Estimated tokens of raw thoughts still kept uncompressed."""
        with self._think_lock:
            return sum(int(e.get("tokens", 0) or 0) for e in self.thinking_archive)

    def thinking_total_tokens(self):
        """Estimated tokens currently held: compressed summary + raw archive."""
        with self._think_lock:
            return _estimate_tokens(self.thinking_summary) + self.thinking_pending_tokens()

    def export_markdown(self):
        lines = [f"# {self.title}", f"Created: {self.created}", ""]
        for msg in self.messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            time_str = msg.get("time", "")
            if role == "user":
                lines.append(f"## 👤 User ({time_str})")
            elif role == "assistant":
                lines.append(f"## 🤖 Assistant ({time_str})")
            elif role == "tool":
                lines.append(f"## 🔧 Tool Result ({time_str})")
            else:
                lines.append(f"## {role} ({time_str})")
            lines.append("")
            lines.append(content)
            lines.append("")
        return "\n".join(lines)

def list_conversations():
    """List all saved conversations, sorted by modification time (newest first)."""
    def _mtime(p):
        try:
            return p.stat().st_mtime
        except OSError:
            return 0  # File vanished between glob and stat: don't crash the listing
    convs = []
    for p in sorted(CONV_DIR.glob("*.json"), key=_mtime, reverse=True):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            msgs = data.get("messages", [])
            convs.append({
                "id": data.get("id", p.stem),
                "title": data.get("title", "Untitled"),
                "created": data.get("created", ""),
                "count": len(msgs),
                "tokens": _estimate_messages_tokens(msgs),
                "path": p,
            })
        except Exception:
            continue
    return convs


# ── §11b CHAIN-OF-THOUGHT MEMORY (26w35a+) ─────────────────────
# Rules (per user spec):
#  • Every round's reasoning is retained and presented back to the LLM.
#  • Cumulative uncompressed thought tokens are counted silently.
#  • At >= 10000 tokens a BACKGROUND compression starts (parallel, the main
#    conversation does NOT wait). The next round waits for the result, which is
#    sent together with the newest round's raw thought.
#  • While reasoning streams, its OWN per-round token count is tracked
#    independently. At >= 30000 tokens the generation is force-stopped and
#    thoughts are compressed SYNCHRONOUSLY (main conversation waits), then
#    the conversation continues.

def write_thinking_compression_report(entries, prior_summary, new_summary, model="", kind="bg"):
    """Write a markdown report capturing BOTH the original thoughts and the
    compressed summary so the user can open and inspect what was kept/dropped.
    Returns the report path (or None on failure).

    The compressed summary itself stays in conv.thinking_summary and is ALWAYS
    counted in thinking tokens and injected into later rounds — never discarded.
    """
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        kind_label = "background" if kind == "bg" else "sync"
        report_path = THINKING_LOG_DIR / f"thinkcompress_{ts}_{kind_label}.md"
        raw_tokens = sum(int(e.get("tokens", 0) or 0) for e in entries) + _estimate_tokens(prior_summary)
        new_tokens = _estimate_tokens(new_summary)
        lines = [
            f"# 🧠 思维链压缩报告 / Chain-of-thought compression report",
            "",
            f"- 时间 / Time: {datetime.now().isoformat()}",
            f"- 方式 / Mode: {'后台异步 background' if kind == 'bg' else '强制同步 synchronous'}",
            f"- 压缩模型 / Model: {model}",
            f"- Token: 原文 ~{raw_tokens} → 摘要 ~{new_tokens}",
            "",
            "---",
            "",
            "## ✅ 压缩后的摘要（会持续保留、计入思维token、注入后续每轮LLM）",
            "",
            (new_summary or "").strip() or "(empty)",
            "",
            "---",
            "",
            "## 📜 压缩前的原始思维链（原文，仅本报告留存）",
            "",
        ]
        if prior_summary and prior_summary.strip():
            lines += ["### [此前已压缩摘要 / Previous summary]", "", prior_summary.strip(), ""]
        for i, e in enumerate(entries, 1):
            lines += [
                f"### [原始思考 {i} / Original thought {i}] (~{int(e.get('tokens', 0) or 0)} tok)",
                "",
                (e.get("text") or "").strip() or "(empty)",
                "",
            ]
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return report_path, raw_tokens, new_tokens
    except Exception:
        return None, 0, 0


def print_thinking_compression_link(report_path, raw_tokens, new_tokens):
    """Print a Ctrl+Click hyperlink to a compression report (main thread only)."""
    if not report_path:
        return
    try:
        launcher = _create_log_launcher(report_path, title="🧠 Thinking compression report")
        link = _make_clickable_link(launcher,
                                    label=t("thinking.compress_report", before=raw_tokens, after=new_tokens))
        console.print(f"  {link}")
    except Exception:
        pass


class ThinkingCompressTask:
    """Background compression of a snapshot of thought entries."""
    def __init__(self, entries, include_summary=""):
        self.entries = entries                       # list of archive entry dicts
        self.include_summary = include_summary       # existing summary to merge
        self.result = None
        self.error = None
        self.done = False
        self.thread = None

    def _run(self):
        try:
            _lang = _get_lang()
            if _lang == "zh":
                sys_ins = ("你是一个思维链压缩器。请把多轮AI思考记录压缩成精炼但完整的思维摘要，"
                           "保留：关键结论、决策与理由、未完成的推导、重要事实/路径/数字、待验证假设。"
                           "删除：重复试错过程、寒暄、已确认错误的分支。用要点形式输出中文摘要。")
                header = "以下是需要压缩的AI思维链记录（含已有摘要）："
            else:
                sys_ins = ("You are a chain-of-thought compressor. Condense multi-round AI "
                           "reasoning into a concise but complete summary preserving: key conclusions, "
                           "decisions and rationale, unfinished reasoning, important facts/paths/numbers, "
                           "open hypotheses. Drop repetitive trials, chatter and disproven branches.")
                header = "AI chain-of-thought records to compress (including any prior summary):"
            parts = []
            if self.include_summary.strip():
                parts.append("[Existing summary]\n" + self.include_summary.strip())
            for i, e in enumerate(self.entries, 1):
                txt = (e.get("text") or "").strip()
                if txt:
                    parts.append(f"[Thought {i}]\n{txt}")
            user_content = header + "\n\n" + "\n\n".join(parts)
            cfg = load_full_config()
            model = get_thinking_compress_model()
            self.result = llm_summarize(sys_ins, user_content, model=model,
                                        full_config=cfg, max_tokens=3000, timeout=180)
            if not self.result:
                self.error = "empty summary"
        except Exception as ex:
            self.error = str(ex)
        finally:
            self.done = True

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def join(self, timeout=None):
        if self.thread:
            self.thread.join(timeout=timeout)
        return self.result if self.done and self.result else None


def _thinking_prompt_block():
    """Instruction block appended to archived thoughts."""
    if _get_lang() == "zh":
        return ("\n\n## 你之前各轮的思维链（保留模式）\n"
                "以下是你此前各轮的思考（已压缩摘要+最近未压缩原文）。"
                "请在此基础上继续思考，继承已有结论，不要重复已完成的推导：\n")
    return ("\n\n## Your chain of thought from previous rounds (retention mode)\n"
            "Below are your earlier thoughts (compressed summary + latest raw thoughts). "
            "Build on them and do not repeat completed reasoning:\n")


def thinking_memory_block(conv):
    """Join any in-flight background compression, then build the thought block
    to inject into the next LLM call's system prompt. Returns '' when disabled."""
    if not is_keep_thinking_enabled():
        return ""
    with conv._think_lock:
        task = conv._think_bg_task
    if task is not None:
        # Spec: the next round WAITS for the compression launched last round.
        result = task.join()
        with conv._think_lock:
            conv._think_bg_task = None
            snapshot = [e for e in conv.thinking_archive if e.get("compressing")]
            if result:
                # Report BEFORE replacing state (task holds prior summary + snapshot)
                report = write_thinking_compression_report(
                    snapshot, task.include_summary, result,
                    model=get_thinking_compress_model(), kind="bg",
                )
                # Merge: older summary + compressed snapshot become the new summary
                conv.thinking_summary = result.strip()
                conv.thinking_archive = [e for e in conv.thinking_archive if not e.get("compressing")]
            else:
                # Failure safety: restore snapshot as raw thoughts, never lose them
                for e in snapshot:
                    e["compressing"] = False
        if result and report and report[0]:
            print_thinking_compression_link(report[0], report[1], report[2])

    with conv._think_lock:
        summary = (conv.thinking_summary or "").strip()
        raw_entries = [e for e in conv.thinking_archive if not e.get("compressing")]
    if not summary and not raw_entries:
        return ""

    parts = [_thinking_prompt_block()]
    if summary:
        parts.append("### 已压缩思维摘要 / Compressed thoughts\n" + summary)
    if raw_entries:
        raw_text = "\n\n".join(
            f"[第{i+1}轮思考 / Thought {i+1}]\n{(e.get('text') or '').strip()}"
            for i, e in enumerate(raw_entries)
        )
        parts.append("### 最近未压缩思维 / Recent raw thoughts\n" + raw_text)
    return "\n".join(parts)


def thinking_memory_record_round(conv, reasoning, reasoning_tokens=0):
    """Record one round's reasoning. If cumulative pending thoughts reach
    get_thinking_compress_trigger_tokens(), launch a BACKGROUND compression.
    The newest round's thought is always kept raw (sent alongside the
    compressed older thoughts on the next call)."""
    if not is_keep_thinking_enabled() or not reasoning or not reasoning.strip():
        return None
    tokens = int(reasoning_tokens or 0) or _estimate_tokens(reasoning)
    with conv._think_lock:
        conv.thinking_archive.append({"text": reasoning, "tokens": tokens, "compressing": False})
        pending = sum(int(e.get("tokens", 0) or 0) for e in conv.thinking_archive)
        already_running = conv._think_bg_task is not None
        if pending < get_thinking_compress_trigger_tokens() or already_running:
            return None
        # Compress everything EXCEPT the newest thought (kept raw for the next call)
        snapshot = [e for e in conv.thinking_archive[:-1] if not e.get("compressing")]
        if not snapshot:
            return None
        for e in snapshot:
            e["compressing"] = True
        task = ThinkingCompressTask(snapshot, include_summary=conv.thinking_summary).start()
        conv._think_bg_task = task
    console.print(f"[dim]{t('thinking.compressing_bg', tokens=get_thinking_compress_trigger_tokens())}[/dim]")
    return task


def thinking_memory_force_compress(conv, reasoning, reasoning_tokens=0):
    """Synchronous compression after a single round exceeded the hard limit
    (30000 tokens). The current giant thought + pending archive + existing
    summary are compressed TOGETHER, and the main conversation waits."""
    tokens = int(reasoning_tokens or 0) or _estimate_tokens(reasoning or "")
    with conv._think_lock:
        # Absorb any background task first
        task = conv._think_bg_task
    if task is not None:
        result = task.join()
        with conv._think_lock:
            conv._think_bg_task = None
            if result:
                # The background compression finished while we waited: apply its
                # result. Ignoring it would re-compress the same entries below.
                snapshot = [e for e in conv.thinking_archive if e.get("compressing")]
                report = None
                try:
                    report = write_thinking_compression_report(
                        snapshot, task.include_summary, result,
                        model=get_thinking_compress_model(), kind="bg",
                    )
                except Exception:
                    pass
                conv.thinking_summary = result.strip()
                conv.thinking_archive = [e for e in conv.thinking_archive if not e.get("compressing")]
                if report and report[0]:
                    print_thinking_compression_link(report[0], report[1], report[2])
            else:
                # Background failed: restore its snapshot for the sync pass
                for e in conv.thinking_archive:
                    if e.get("compressing"):
                        e["compressing"] = False
    with conv._think_lock:
        entries = list(conv.thinking_archive)
        for e in entries:
            e["compressing"] = False
        if reasoning and reasoning.strip():
            entries.append({"text": reasoning, "tokens": tokens, "compressing": False})
        if not entries and not conv.thinking_summary.strip():
            return
        console.print(f"[dim]{t('thinking.compressing_sync')}[/dim]")
        sync_task = ThinkingCompressTask(entries, include_summary=conv.thinking_summary)
    # Run synchronously (caller blocks before continuing the main conversation)
    sync_task._run()
    report = None
    with conv._think_lock:
        if sync_task.result:
            report = write_thinking_compression_report(
                entries, sync_task.include_summary, sync_task.result,
                model=get_thinking_compress_model(), kind="sync",
            )
            conv.thinking_summary = sync_task.result.strip()
            conv.thinking_archive = []
        else:
            # Keep data on failure, but cap the giant raw text to protect the window
            kept = []
            budget = KEEP_RECENT_TOKENS
            for e in reversed(entries):
                t_budget = int(e.get("tokens", 0) or 0) or _estimate_tokens(e.get("text", ""))
                if budget - t_budget < 0:
                    break
                budget -= t_budget
                kept.insert(0, e)
            conv.thinking_archive = kept
    console.print(f"[success]{t('thinking.compressed_sync', tokens=conv.thinking_total_tokens())}[/success]")
    if sync_task.result and report and report[0]:
        print_thinking_compression_link(report[0], report[1], report[2])

# ════════════════════════════════════════════════════════════════
# §12 AGENT LOOP
#     Main interaction loop, command handler, startup picker
# ════════════════════════════════════════════════════════════════

def _fuzzy_match_conversations(query, convs):
    """Fuzzy match conversation titles by name. Returns list of matching convs."""
    query_lower = query.lower().strip()
    if not query_lower:
        return []
    # Exact match first
    exact = [c for c in convs if c['title'].lower() == query_lower]
    if exact:
        return exact
    # Starts with
    starts = [c for c in convs if c['title'].lower().startswith(query_lower)]
    if starts:
        return starts
    # Contains
    contains = [c for c in convs if query_lower in c['title'].lower()]
    if contains:
        return contains
    # Any word match
    query_words = query_lower.split()
    word_matches = [c for c in convs if any(w in c['title'].lower() for w in query_words)]
    if word_matches:
        return word_matches
    return []

def startup_picker():
    """Interactive conversation picker at startup (name-based matching from 26w03a)."""
    convs = list_conversations()
    if not convs:
        return None

    console.print(f"\n[bold]{t('picker.title')}[/bold]")
    console.print(f"  {t('picker.new_hint')}")
    for c in convs[:10]:
        console.print(f"  📂 {c['title']} (~{c['tokens']} tok) [{c['id'][:8]}]")

    while True:
        try:
            choice = input(t("picker.prompt")).strip()
            if not choice:
                return None  # New conversation
            matches = _fuzzy_match_conversations(choice, convs)
            if len(matches) == 1:
                conv = Conversation.load(matches[0]["path"])
                _tok = _estimate_messages_tokens(conv.messages)
                _loaded_msg = f"已打开: {conv.title} (~{_tok} tok)" if _get_lang() == "zh" else f"Opened: {conv.title} (~{_tok} tok)"
                console.print(f"[success]{_loaded_msg}[/success]")
                return conv
            elif len(matches) > 1:
                console.print(f"[warning]{t('picker.ambiguous', count=len(matches))}[/warning]")
                for m in matches[:5]:
                    console.print(f"  📂 {m['title']} (~{m['tokens']} tok)")
            else:
                console.print(f"[warning]{t('picker.invalid')}[/warning]")
        except (EOFError, KeyboardInterrupt):
            return None

def agent_loop(conv):
    """Main agent interaction loop."""
    # Per-conversation prompt (/sys command writes conv.system_prompt) takes
    # priority; otherwise fall back to the global config prompt
    system_prompt = (getattr(conv, "system_prompt", "") or "").strip() or get_system_prompt()
    # Append context instructions if any
    ctx = load_context_instructions()
    if ctx:
        system_prompt = system_prompt + "\n\n## Project Context\n" + ctx

    # Inject current multi-model configuration into system prompt
    full_config = load_full_config()
    main_model = full_config.get("model", "glm-4-flash")
    mm_model = full_config.get("multimodalModel", "")
    backup_model = full_config.get("backupModel", "")
    img_model = full_config.get("imageGenModel", "cogview-3-flash")
    vid_model = full_config.get("videoGenModel", "cogvideox-flash")
    sub_models = full_config.get("subagentModels", {})

    model_info = f"\n\n## Current Model Configuration\n"
    model_info += f"- Main Agent: {main_model} ({', '.join(sorted(get_model_capabilities(main_model)))})\n"
    if mm_model:
        mm_caps = get_model_capabilities(mm_model)
        mm_caps_str = ', '.join(sorted(mm_caps)) or "text"
        model_info += f"- Multimodal (for image_view/video_view): {mm_model} ({mm_caps_str})\n"
    elif is_multimodal_model(main_model):
        model_info += f"- Multimodal: Using main model (it supports vision)\n"
    else:
        model_info += f"- Multimodal: NOT CONFIGURED (image_view/video_view will fail)\n"
    if backup_model:
        model_info += f"- Backup: {backup_model}\n"
    model_info += f"- Image Generation: {img_model}\n"
    model_info += f"- Video Generation: {vid_model}\n"
    if sub_models:
        model_info += f"- Sub-Agent Models (use these EXACT names as model parameter in task tool):\n"
        for name, mid in sub_models.items():
            caps = ", ".join(sorted(get_model_capabilities(mid))) or "text"
            model_info += f"  - '{name}': {mid} ({caps})\n"
    else:
        model_info += f"- Sub-Agent Models: None configured — YOU MUST ask the user to configure sub-agent models first!\n"
    system_prompt += model_info

    esc_hint = t("esc.hint")
    perms = get_permissions()
    max_rounds = perms.get("maxRounds", 20) or 20

    while True:
        _reset_esc_stop()
        try:
            # Get user input
            prompt_str = f"[bold cyan]zagent[/] [dim]{get_cwd()}[/]"
            user_input = console.input(f"{prompt_str} ").strip()
        except EOFError:
            break
        except KeyboardInterrupt:
            break

        if not user_input:
            continue

        # Handle commands
        _is_clipboard_paste = False
        if user_input.startswith("/"):
            should_continue = handle_command(user_input, conv)
            # Check for clipboard paste signal
            if isinstance(should_continue, tuple) and should_continue[0] == "__clipboard__":
                user_input = should_continue[1]
                _is_clipboard_paste = True
                # Fall through to process as normal user input below
            elif should_continue is False:
                break
            else:
                continue

        # If clipboard paste content starts with /, it's NOT a command — treat as normal input
        if user_input.startswith("/") and _is_clipboard_paste:
            pass  # Treat as normal user message, don't re-parse as command

        # Add user message
        conv.add("user", user_input)
        _start_esc_listener()

        # Per-user-turn work log (quiet-tools mode accumulates ALL tool outputs here)
        _work_log_path = None
        _work_log_file = None
        # IDE-style single status line for the whole work phase (quiet mode)
        _work_status = _WorkStatus()
        if is_quiet_tools_enabled():
            _work_status.start()

        def _work_summary_link():
            """One dim line after the final answer: elapsed time + work log link."""
            if not _work_log_path:
                return
            try:
                launcher = _create_log_launcher(_work_log_path, title=f"🛠 {t('quiet.worklog')}")
                link = _make_clickable_link(launcher, label=t("quiet.view"))
                console.print(f"[dim]📁 {t('quiet.worked', time=_work_status.elapsed_str())} | {link}[/dim]")
            except Exception:
                pass

        def _work_log_link(file_path, label):
            """Write a clickable link line into the work log (rich markup — the
            new-terminal viewer prints it with rich, so it's Ctrl+Clickable
            there). No browser involved."""
            if not _work_log_append("", _skip_empty=True):
                return
            try:
                uri = Path(file_path).resolve().as_uri()
                _work_log_file.write(f"🔗 [link={uri}]{label}[/link]\n")
                _work_log_file.flush()
            except Exception:
                pass

        def _work_log_append(chunk, _skip_empty=False):
            nonlocal _work_log_path, _work_log_file
            if not is_quiet_tools_enabled():
                return False
            try:
                if _work_log_file is None:
                    _work_log_path = WORK_LOG_DIR / f"work_{int(time.time()*1000)}.log"
                    _work_log_file = open(_work_log_path, "w", encoding="utf-8")
                    _work_log_file.write(f"🛠 {t('quiet.worklog')} - {datetime.now().isoformat()}\n")
                    _work_log_file.write(f"👤 {user_input[:500]}\n" + "=" * 60 + "\n\n")
                    # The status line becomes a hyperlink to the log launcher
                    # (opens the Rich viewer in a new terminal — NOT a browser)
                    _work_status.set_link(_create_log_launcher(_work_log_path, title=f"🛠 {t('quiet.worklog')}"))
                if chunk:
                    _work_log_file.write(chunk)
                    _work_log_file.flush()
                return True
            except Exception:
                return False

        # Expose the hooks to call_llm_direct (429/retry logging, live activity)
        global _CURRENT_WORK_STATUS, _QUIET_WORK_LOG
        if is_quiet_tools_enabled():
            _CURRENT_WORK_STATUS = _work_status
            _QUIET_WORK_LOG = _work_log_append
            _work_log_append(f"\n=== {t('quiet.workstart')} ===\n")  # eager: link available now
        else:
            _CURRENT_WORK_STATUS = None
            _QUIET_WORK_LOG = None

        def _work_link_line(name, ok=True):
            """Per-tool lines stay in the work log ONLY — the TUI shows the single
            work status line (quiet mode); no per-tool console output."""
            if not _work_log_path:
                return
            try:
                mark = "✅" if ok else "⚠️"
                _work_log_append(f"{mark} {t('quiet.done', name=name)}\n")
            except Exception:
                pass

        # Agent loop (multi-turn tool calling)
        round_num = 0
        while round_num < max_rounds:
            round_num += 1
            if _check_esc_stop():
                if is_quiet_tools_enabled():
                    _work_status.stop()
                    _work_summary_link()
                console.print(f"\n[warning]{t('esc.stopped')}[/warning]")
                conv.save()
                break

            # Wait for / merge any background thought compression, then build
            # the chain-of-thought block presented to the LLM this round.
            think_block = thinking_memory_block(conv)
            active_system_prompt = system_prompt + think_block

            # Context compression check before each API call (token-based)
            threshold = get_context_compression_threshold()
            estimated_tokens = _estimate_messages_tokens(conv.messages)
            if estimated_tokens > threshold:
                compressed = compress_context(conv.messages, active_system_prompt)
                conv.messages = compressed
                conv.save()
                estimated_tokens = _estimate_messages_tokens(conv.messages)

            # Show thinking indicator with token budget + clickable log link
            # Quiet mode: TUI stays clean (only the work status line) — log the info instead
            _thinking_log_path_pre = THINKING_LOG_DIR / f"thinking_{int(time.time()*1000)}.log"
            _launcher_pre = _create_log_launcher(_thinking_log_path_pre, title=f"Thinking - {APP_NAME}")
            _link_pre = _make_clickable_link(_launcher_pre, label=t("thinking.view_hint"))
            # Explicit token labels:
            #  💬上下文 = conversation history tokens / compression threshold
            #     (threshold = 80% of the model's full context window)
            #  🧠思维  = retained chain-of-thought = 已压缩摘要 + 未压缩原文
            _ctx_window = MODEL_CONTEXT_WINDOWS.get(full_config.get("model", ""), 128000)
            _tok_hint = f"💬上下文 ~{estimated_tokens}/{threshold} tok (窗口{_ctx_window//1000}K)"
            if is_keep_thinking_enabled():
                _sum_tok = _estimate_tokens(conv.thinking_summary)
                _raw_tok = conv.thinking_pending_tokens()
                _tok_hint += f" | 🧠思维 ~{_sum_tok + _raw_tok} tok (摘要~{_sum_tok}+原文~{_raw_tok})"
            if is_quiet_tools_enabled():
                _work_status.set_round(round_num)
                _work_status.set_activity("💬 " + ("思考中" if _get_lang() == "zh" else "LLM"))
                _work_log_append(f"\n--- {t('thinking', n=round_num, esc=esc_hint)} [{_tok_hint}] ---\n")
                # Clickable link inside the work log -> this round's live thinking log
                _work_log_link(_launcher_pre, t("quiet.think_log", n=round_num))
            else:
                console.print(f"\n[dim]{t('thinking', n=round_num, esc=esc_hint)} [{_tok_hint}] | {_link_pre}[/dim]")

            _llm_result = call_llm_direct(
                conv.messages, system_prompt=active_system_prompt, use_tools=True, thinking_log_path=_thinking_log_path_pre
            )
            text, tool_calls, error, already_displayed = _llm_result[:4]
            _meta = _llm_result[4] if len(_llm_result) > 4 else {}

            if error:
                # THINKING_FORCE_STOP immediately resumes (start() below), so
                # don't tear down the status line / print a summary link first —
                # that produced a duplicate summary + reset timer every time
                if is_quiet_tools_enabled() and error != "THINKING_FORCE_STOP":
                    _work_status.stop()
                    _work_summary_link()
                if error == "ESC_STOP":
                    console.print(f"\n[warning]{t('esc.stopped')}[/warning]")
                    conv.save()
                    break
                if error == "THINKING_FORCE_STOP":
                    # Single round's reasoning hit the hard limit: discard the partial
                    # generation, synchronously compress thoughts (main loop WAITS),
                    # then continue the conversation on the same user message.
                    if is_quiet_tools_enabled():
                        _work_status.start()  # resume working status for the retry
                    _reset_thinking_force_stop()
                    console.print(f"\n[yellow]🧠 {t('thinking.force_stop', limit=get_thinking_single_round_force_tokens())}[/yellow]")
                    thinking_memory_force_compress(
                        conv,
                        _meta.get("reasoning", ""),
                        _meta.get("reasoning_tokens", 0),
                    )
                    conv.save()
                    continue
                console.print(Panel(f"[danger]{t('llm.error', err=error)}[/danger]", border_style="red"))
                break

            if not text and not tool_calls:
                if is_quiet_tools_enabled():
                    _work_status.stop()
                    _work_summary_link()
                console.print(f"[dim]{t('empty_response')}[/dim]")
                break

            # Retain this round's reasoning (may trigger background compression).
            # Recorded once per LLM call, including tool-call rounds.
            if _meta.get("reasoning"):
                thinking_memory_record_round(
                    conv, _meta["reasoning"], _meta.get("reasoning_tokens", 0)
                )

            # Display assistant text (skip Panel if SSE streaming already displayed it)
            # Quiet mode: intermediate rounds (with tool calls) go to the work log only;
            # the final no-tool-call round is the answer — stop status, show summary + panel.
            if text and text.strip():
                conv.add("assistant", text.strip())
                if is_quiet_tools_enabled():
                    if tool_calls:
                        _work_log_append(f"\n🤖 ASSISTANT (round {round_num}):\n{text.strip()}\n")
                    else:
                        _work_status.stop()
                        _work_summary_link()
                        try:
                            console.print(Panel(
                                Markdown(text.strip()),
                                title=f"🤖 {APP_NAME}",
                                border_style="green",
                                padding=(1, 2),
                            ))
                        except Exception:
                            console.print(Panel(text.strip(), title=f"🤖 {APP_NAME}", border_style="green", padding=(1, 2)))
                elif not already_displayed:
                    console.print(Panel(
                        Markdown(text.strip()),
                        title=f"🤖 {APP_NAME}",
                        border_style="green",
                        padding=(1, 2),
                    ))

            # Execute tool calls
            if tool_calls:
                # Phase 1: Execute non-task tools and spawn task sub-agents
                task_runners = []
                for tc in tool_calls:
                    if _check_esc_stop():
                        if is_quiet_tools_enabled():
                            _work_status.stop()
                            _work_summary_link()
                        console.print(f"\n[warning]{t('esc.stopped')}[/warning]")
                        conv.save()
                        break

                    name = tc.get("name", "")
                    args = tc.get("args", {})
                    tc_id = tc.get("id", "")

                    if name == "task":
                        # Spawn sub-agent without blocking
                        description = args.get("description", "Sub-task")
                        prompt = args.get("prompt", "")
                        resume_id = args.get("resume")
                        sa_model = args.get("model")  # Sub-agent model name
                        # Fallback: if no prompt but has description, use description as prompt
                        if not prompt and description and description != "Sub-task":
                            prompt = description
                        if not prompt:
                            result = "Error: no prompt specified for sub-agent. Please provide a 'prompt' parameter with detailed instructions."
                        else:
                            perms = get_permissions()
                            max_sub = perms.get("maxSubAgents", 3)
                            if max_sub == 0:
                                result = t("subagent.disabled")
                            else:
                                runner, err = _spawn_subagent(description, prompt, resume_subagent_id=resume_id, subagent_model=sa_model)
                                if err:
                                    result = err
                                else:
                                    task_runners.append((tc, runner))
                                    # Show clickable link with sub-agent ID for potential resume
                                    model_hint = f" [{runner.model}]" if runner and runner.model else ""
                                    resume_hint = f" (ID:#{runner.subagent_id})" if runner else ""
                                    launcher_path = _create_log_launcher(runner.log_path, title=f"🤖 Sub-agent: {description[:30]}")
                                    link = _make_clickable_link(launcher_path, label=t("subagent.view_hint"))
                                    if is_quiet_tools_enabled():
                                        _work_log_append(f"🤖 TASK: {description} {resume_hint}{model_hint}\n")
                                        # Clickable link inside the work log -> sub-agent live log
                                        _work_log_link(launcher_path, f"🤖 {description[:40]} {resume_hint}")
                                    else:
                                        console.print(f"  {link}[dim]{resume_hint}{model_hint}[/dim]")
                                    continue  # Don't process result yet

                    # Non-task tools: execute immediately
                    args_display = json.dumps(args, ensure_ascii=False)
                    if len(args_display) > 80:
                        args_display = args_display[:77] + "..."

                    if is_quiet_tools_enabled():
                        # Quiet mode: the single work status line owns the display;
                        # output goes ONLY to the work log. ESC aborts the wait at once.
                        _work_status.set_activity(f"🔧 {name}")
                        _work_log_append(
                            f"\n🔔 TOOL_CALL: {name} {args_display}\n"
                            + "-" * 60 + "\n"
                        )
                        try:
                            result = _spin_while(
                                execute_tool, None, name, args, show_spinner=False
                            )
                        except Exception as _te:
                            result = f"Tool execution error: {_te}"
                        if result == "__ESC_STOP__":
                            _work_log_append(f"⏹ ESC interrupted while running {name} (moved to background)\n")
                            _work_link_line(f"{name} {args_display[:40]}", ok=False)
                            break
                        _work_log_append(f"{result}\n" + "=" * 60 + "\n")
                        _work_link_line(f"{name} {args_display[:40]}",
                                        ok=not str(result).startswith("Tool execution error"))
                    else:
                        console.print(f"\n[tool]{t('tool_call', name=name, args=args_display)}[/tool]")

                        result = execute_tool(name, args)

                        result_display = result
                        if len(result_display) > 2000:
                            result_display = result_display[:1000] + "\n... (truncated) ...\n" + result_display[-1000:]

                        # Try Markdown rendering for tool results
                        panel_title = f"📋 {t('tool_result', name=name)}"
                        panel_border = "blue"
                        try:
                            console.print(Panel(
                                Markdown(result_display),
                                title=panel_title,
                                border_style=panel_border,
                                padding=(1, 2),
                            ))
                        except Exception:
                            console.print(Panel(
                                result_display,
                                title=panel_title,
                                border_style=panel_border,
                                padding=(1, 2),
                            ))

                    # Store in conversation with raw API format.
                    # content is empty: the assistant text was already stored once
                    # above — repeating it in every tool_call's raw message would
                    # duplicate it N+1 times in the API history
                    conv.add("assistant", "", _raw_api_msg={
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{
                            "id": tc_id,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}
                        }]
                    } if tc_id else None)
                    conv.add("tool", result, tool_call_id=tc_id)

                # Phase 2: Wait for all spawned sub-agents to complete (with spinner)
                if task_runners:
                    if is_quiet_tools_enabled():
                        _work_status.set_activity(f"🤖 {task_runners[0][1].description[:30] if task_runners[0][1] else ''}")
                    _spin_subagents(task_runners)
                    if _check_esc_stop():
                        # Sub-agents keep running in background; stop this turn now
                        if is_quiet_tools_enabled():
                            _work_status.stop()
                            _work_summary_link()
                        console.print(f"\n[warning]{t('esc.stopped')}[/warning]")
                        conv.save()
                        break
                    for tc, runner in task_runners:
                        tc_id = tc.get("id", "")
                        name = "task"
                        description = tc.get("args", {}).get("description", "Sub-task")
                        sa_id_display = f"#{runner.subagent_id}" if runner else "?"
                        if runner and runner.result:
                            result = runner.result
                        elif runner and runner.error:
                            result = f"Sub-agent error: {runner.error}"
                        else:
                            result = "Sub-agent completed"

                        # Append resume hint to result so LLM knows it can resume
                        result_with_hint = result + f"\n\n[Sub-agent ID: {sa_id_display}. Use resume={runner.subagent_id} in the task tool to continue this sub-agent if needed.]"

                        if is_quiet_tools_enabled():
                            # Collapse sub-agent output too: work log only
                            _sa_ok = not (runner and runner.error)
                            try:
                                launcher = _create_log_launcher(runner.log_path, title=f"🤖 {description[:40]}")
                                _work_log_append(f"{'✅' if _sa_ok else '⚠️'} 🤖 {description[:50]} {sa_id_display} | {launcher}\n")
                            except Exception:
                                pass
                        else:
                            result_display = result
                            if len(result_display) > 2000:
                                result_display = result_display[:1000] + "\n... (truncated) ...\n" + result_display[-1000:]

                            panel_title = f"🤖 {t('tool_result', name=description[:40])}"
                            try:
                                console.print(Panel(
                                    Markdown(result_display),
                                    title=panel_title,
                                    border_style="cyan",
                                    padding=(1, 2),
                                ))
                            except Exception:
                                console.print(Panel(
                                    result_display,
                                    title=panel_title,
                                    border_style="cyan",
                                    padding=(1, 2),
                                ))

                        # Store in conversation
                        conv.add("assistant", "", _raw_api_msg={
                            "role": "assistant",
                            "content": text or "",
                            "tool_calls": [{
                                "id": tc_id,
                                "type": "function",
                                "function": {"name": name, "arguments": json.dumps(tc.get("args", {}), ensure_ascii=False)}
                            }]
                        } if tc_id else None)
                        conv.add("tool", result_with_hint, tool_call_id=tc_id)

                if is_quiet_tools_enabled():
                    _work_log_append(f"\n{t('continue_process', esc=esc_hint)}\n")
                else:
                    console.print(f"[dim]{t('continue_process', esc=esc_hint)}[/dim]")
                continue  # Continue agent loop for next round
            else:
                break  # No tool calls, done

        # Turn over: make sure the work status line is gone and the log is closed
        _CURRENT_WORK_STATUS = None
        _QUIET_WORK_LOG = None
        if is_quiet_tools_enabled():
            _work_status.stop()
        if _work_log_file:
            try:
                _work_log_file.write("\n" + "=" * 60 + f"\n✔ {t('quiet.turn_done')}\n")
                _work_log_file.close()
            except Exception:
                pass
            _work_log_file = None
        conv.save()

def handle_command(user_input, conv):
    """Handle slash commands. Returns False to exit."""
    parts = user_input.strip().split(None, 1)
    command = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if command in ("/help", "/h", "/?"):
        show_help()

    elif command in ("/new", "/n"):
        name = arg.strip()
        conv.save()
        conv.__init__()
        if name:
            conv.title = name
            console.print(f"[success]{t('cmd.new_named', name=name)}[/success]")
        else:
            # Ask for name
            try:
                name = input(t("picker.input_name")).strip()
                if name:
                    conv.title = name
                    console.print(f"[success]{t('cmd.new_named', name=name)}[/success]")
                else:
                    console.print(f"[success]{t('cmd.new_done')}[/success]")
            except (EOFError, KeyboardInterrupt):
                console.print(f"[success]{t('cmd.new_done')}[/success]")

    elif command in ("/list", "/ls", "/l"):
        convs = list_conversations()
        if not convs:
            console.print(f"[dim]{t('cmd.no_convs')}[/dim]")
        else:
            console.print(f"[bold]{t('cmd.conv_saved')}:[/bold]")
            for i, c in enumerate(convs, 1):
                console.print(f"  {i}. {c['title']} (~{c['tokens']} tok) [{c['id'][:8]}]")

    elif command in ("/open", "/o"):
        convs = list_conversations()
        if not convs:
            console.print(f"[dim]{t('cmd.no_convs')}[/dim]")
        else:
            for c in convs[:10]:
                console.print(f"  📂 {c['title']} (~{c['tokens']} tok)")
            if arg:
                choice = arg
            else:
                try:
                    choice = input(t("picker.prompt")).strip()
                except (EOFError, KeyboardInterrupt):
                    return True
            if not choice:
                return True
            matches = _fuzzy_match_conversations(choice, convs)
            if len(matches) == 1:
                conv.save()
                loaded = Conversation.load(matches[0]["path"])
                conv.__dict__.update(loaded.__dict__)
                _open_tok = _estimate_messages_tokens(conv.messages)
                _open_msg = f"已打开: {conv.title} (~{_open_tok} tok)" if _get_lang() == "zh" else f"Opened: {conv.title} (~{_open_tok} tok)"
                console.print(f"[success]{_open_msg}[/success]")
            elif len(matches) > 1:
                console.print(f"[warning]{t('picker.ambiguous', count=len(matches))}[/warning]")
                for m in matches[:5]:
                    console.print(f"  📂 {m['title']} (~{m['tokens']} tok)")
            else:
                console.print(f"[warning]{t('picker.invalid')}[/warning]")

    elif command in ("/delete", "/del", "/d"):
        convs = list_conversations()
        if not convs:
            console.print(f"[dim]{t('cmd.no_convs')}[/dim]")
        else:
            for c in convs[:10]:
                console.print(f"  📂 {c['title']} (~{c['tokens']} tok)")
            if arg:
                choice = arg
            else:
                try:
                    choice = input(t("cmd.delete_name")).strip()
                except (EOFError, KeyboardInterrupt):
                    console.print(f"[dim]{t('cmd.delete_cancel')}[/dim]")
                    return True
            if not choice:
                console.print(f"[dim]{t('cmd.delete_cancel')}[/dim]")
                return True
            matches = _fuzzy_match_conversations(choice, convs)
            if len(matches) == 0:
                console.print(f"[warning]{t('cmd.delete_not_found', name=choice)}[/warning]")
            elif len(matches) > 1:
                console.print(f"[warning]{t('cmd.delete_multiple', count=len(matches))}[/warning]")
                for m in matches[:5]:
                    console.print(f"  📂 {m['title']} (~{m['tokens']} tok)")
            else:
                target = matches[0]
                # Refuse to delete the ACTIVE conversation: conv.save() on the
                # next round would re-create the file ("resurrecting" it)
                try:
                    active_path = os.path.normcase(os.path.abspath(str(CONV_DIR / f"{conv.id}.json")))
                    if os.path.normcase(os.path.abspath(str(target["path"]))) == active_path:
                        console.print("[warning]⚠️ 不能删除当前正在使用的会话 (请先 /new 或切换会话)[/warning]")
                        return True
                except Exception:
                    pass
                target["path"].unlink()
                console.print(f"[success]{t('cmd.delete_done', title=target['title'])}[/success]")

    elif command in ("/rename", "/ren"):
        try:
            new_name = input(t("cmd.rename_prompt")).strip()
            if new_name:
                conv.title = new_name
                conv.save()
                console.print(f"[success]{t('cmd.rename_done', name=new_name)}[/success]")
            else:
                console.print(f"[dim]{'未修改' if _get_lang() == 'zh' else 'Not renamed'}[/dim]")
        except (EOFError, KeyboardInterrupt):
            pass

    elif command in ("/export", "/ex"):
        export_dir = ZAGENT_HOME / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        safe_title = re.sub(r'[^\w\s-]', '', conv.title).strip().replace(' ', '_')[:50]
        path = export_dir / f"{safe_title}_{conv.id}.md"
        with open(path, "w", encoding="utf-8") as f:
            f.write(conv.export_markdown())
        console.print(f"[success]{t('cmd.exported', path=path)}[/success]")

    elif command in ("/sys", "/system"):
        if arg:
            conv.system_prompt = arg
            conv.save()
            console.print(f"[success]{t('cmd.sys_updated')}[/success]")
        else:
            console.print(Panel(conv.system_prompt, title=t("cmd.sys_prompt"), border_style="blue"))

    elif command in ("/cd",):
        if arg:
            target = arg.strip().strip('"').strip("'")
            # Expand ~ to home dir
            if target.startswith("~"):
                target = os.path.expanduser(target)
            if os.path.isdir(target):
                set_cwd(target)
                # Also save as default workDir in config
                cfg = load_full_config()
                cfg["workDir"] = get_cwd()
                save_config(cfg)
                console.print(f"[success]📂 {t('cmd.cd_changed', path=get_cwd())}[/success]")
            else:
                console.print(f"[warning]{t('cmd.cd_not_found', path=target)}[/warning]")
        else:
            console.print(f"📂 {get_cwd()}")

    elif command in ("/clear", "/cls"):
        os.system("cls" if IS_WINDOWS else "clear")

    elif command in ("/cv", "/clipboard", "/paste"):
        # Paste from clipboard as input (avoids multi-line auto-send issue)
        clip_text = _read_clipboard()
        if clip_text is None:
            console.print(f"[warning]{t('clipboard.error', err='no clipboard support')}[/warning]")
        elif not clip_text.strip():
            console.print(f"[dim]{t('clipboard.empty')}[/dim]")
        else:
            n_lines = clip_text.strip().count('\n') + 1
            console.print(f"[success]{t('clipboard.pasted', n=n_lines)}[/success]")
            # Show a preview
            preview = clip_text.strip()
            if len(preview) > 200:
                preview = preview[:200] + "..."
            console.print(Panel(preview, title="📋 Clipboard", border_style="cyan", padding=(0, 1)))
            # Return the clipboard content as a special signal for the main loop
            return ("__clipboard__", clip_text.strip())

    elif command in ("/info", "/i"):
        env_note = f"\n  💡 {t('cmd.conv_dir_env')}" if os.environ.get("ZAGENT_HOME") else ""
        perms = get_permissions()
        console.print(Panel(
            f"[bold]{t('cmd.conv_info')}:[/bold]\n\n"
            f"  🏠 ZAGENT_HOME: {ZAGENT_HOME}{env_note}\n"
            f"  📂 {t('help.list')}: {CONV_DIR}\n"
            f"  📄 config: {LOCAL_CONFIG}\n"
            f"  🔒 permissions: {PERM_CONFIG}\n"
            f"  💬 {t('cmd.conv_saved')}: {len(list_conversations())}\n"
            f"  🆔 {t('cmd.current')}对话: {conv.title}\n"
            f"  📝 上下文: ~{_estimate_messages_tokens(conv.messages)} tokens ({len(conv.messages)} msgs)\n"
            f"  📦 压缩阈值: ~{get_context_compression_threshold()} tokens\n"
            f"  🧠 思维链保留: {t('thinkkeep.status_on') if is_keep_thinking_enabled() else t('thinkkeep.status_off')}"
            f" ({get_thinking_compress_model()}, 摘要~{_estimate_tokens(conv.thinking_summary)}+原文~{conv.thinking_pending_tokens()}"
            f"=~{conv.thinking_total_tokens()} tok)\n"
            f"  🔇 工作输出隐藏: {t('quiet.status_on') if is_quiet_tools_enabled() else t('quiet.status_off')} (/quiet on|off)\n"
            f"  🐚 Shell: {SHELL_NAME} ({SHELL_PATH})\n"
            f"  📂 CWD: {get_cwd()}\n"
            f"  🖥  Platform: {platform.system()} {platform.release()}\n"
            f"  🔒 Write: {'✅' if perms.get('allowWrite') else '❌'}  "
            f"Delete: {'✅' if perms.get('allowDelete') else '❌'}  "
            f"Execute: {'✅' if perms.get('allowExecute') else '❌'}  "
            f"Network: {'✅' if perms.get('allowNetwork') else '❌'}",
            title=f"ℹ️ {t('cmd.conv_info')}", border_style="magenta"
        ))

    elif command in ("/model", "/m"):
        config = load_zai_config()
        if not config:
            console.print(f"[warning]{t('cmd.no_config')}[/warning]")
            return True
        if arg:
            # Support /model <model> for main model
            # Support /model mm <model> for multimodal model
            # Support /model backup <model> for backup model
            # Support /model mm-backup <model> for backup multimodal model
            # Support /model img <model> for image generation model
            # Support /model vid <model> for video generation model
            parts = arg.split(None, 1)
            cfg = load_full_config()

            def _custom_note(mname):
                """Dim note when a model name is not in the built-in registry."""
                if mname and mname not in AVAILABLE_MODELS:
                    console.print(f"[dim]↳ 自定义模型（未内置注册，将使用当前 baseUrl + 匹配的 Key 直接调用）: {mname}[/dim]")

            if len(parts) == 2 and parts[0] in ("mm", "multimodal"):
                model_name = parts[1]
                # ANY model name is accepted (custom models work via current endpoint + key)
                cfg["multimodalModel"] = model_name
                save_config(cfg)
                if model_name:
                    _custom_note(model_name)
                    console.print(f"[success]Multimodal model set to: {model_name} ({model_desc(model_name)})[/success]")
                else:
                    console.print("[success]Multimodal model cleared[/success]")
            elif len(parts) == 2 and parts[0] in ("backup", "bk"):
                model_name = parts[1]
                cfg["backupModel"] = model_name
                save_config(cfg)
                if model_name:
                    _custom_note(model_name)
                    console.print(f"[success]Backup model set to: {model_name} ({model_desc(model_name)})[/success]")
                else:
                    console.print("[success]Backup model cleared[/success]")
            elif len(parts) == 2 and parts[0] in ("mm-backup", "mm-bk"):
                model_name = parts[1]
                cfg["backupMultimodalModel"] = model_name
                save_config(cfg)
                if model_name:
                    _custom_note(model_name)
                    console.print(f"[success]Backup multimodal model set to: {model_name} ({model_desc(model_name)})[/success]")
                else:
                    console.print("[success]Backup multimodal model cleared[/success]")
            elif len(parts) == 2 and parts[0] in ("img", "image"):
                model_name = parts[1]
                cfg["imageGenModel"] = model_name
                save_config(cfg)
                if model_name:
                    _custom_note(model_name)
                    console.print(f"[success]Image generation model set to: {model_name} ({model_desc(model_name)})[/success]")
                else:
                    console.print("[success]Image generation model cleared (will use cogview-3-flash)[/success]")
            elif len(parts) == 2 and parts[0] in ("vid", "video"):
                model_name = parts[1]
                cfg["videoGenModel"] = model_name
                save_config(cfg)
                if model_name:
                    _custom_note(model_name)
                    console.print(f"[success]Video generation model set to: {model_name} ({model_desc(model_name)})[/success]")
                else:
                    console.print("[success]Video generation model cleared (will use cogvideox-flash)[/success]")
            elif len(parts) == 2 and parts[0] in ("sub", "subagent"):
                # /model sub <name> <model_id> — add a named sub-agent model
                sub_parts = parts[1].split(None, 1)
                if len(sub_parts) == 2:
                    sub_name, sub_model = sub_parts
                    if "subagentModels" not in cfg:
                        cfg["subagentModels"] = {}
                    cfg["subagentModels"][sub_name] = sub_model
                    save_config(cfg)
                    console.print(f"[success]Sub-agent model '{sub_name}' set to: {sub_model}[/success]")
                else:
                    # Show current sub-agent models
                    subagent_models = cfg.get("subagentModels", {})
                    if subagent_models:
                        table = Table(title="🤖 Sub-Agent Models")
                        table.add_column("Name", style="cyan")
                        table.add_column("Model", style="yellow")
                        table.add_column("Capabilities", style="green")
                        for name, mid in subagent_models.items():
                            caps = ", ".join(sorted(get_model_capabilities(mid))) or "text"
                            table.add_row(name, mid, caps)
                        console.print(table)
                    else:
                        console.print("[dim]No sub-agent models configured. Use: /model sub <name> <model_id>[/dim]")
            else:
                # Main model: ANY model name works — registered providers auto-switch
                # baseUrl; custom names are called against the current endpoint + key.
                model_name = arg.strip()
                cfg["model"] = model_name
                prov_id, prov_info = find_provider_for_model(model_name)
                if prov_info:
                    new_base = prov_info["baseUrl"]
                    old_base = cfg.get("baseUrl", "")
                    if old_base.rstrip("/") != new_base.rstrip("/"):
                        cfg["baseUrl"] = new_base
                        console.print(f"[dim]{t('provider.auto_baseUrl', url=new_base)}[/dim]")
                else:
                    console.print(f"[dim]↳ 自定义模型（未内置注册，将使用当前 baseUrl + 匹配的 Key 直接调用）: {model_name}[/dim]")
                save_config(cfg)
                console.print(f"[success]{t('cmd.model_changed', model=model_name, desc=model_desc(model_name))}[/success]")
        else:
            current = config.get("model", "unknown")
            full_cfg = load_full_config()
            # Show models grouped by provider
            current_prov_id, _ = find_provider_for_model(current)
            table = Table(title=t("cmd.model_info"))
            table.add_column("Provider", style="yellow")
            table.add_column("Model", style="cyan")
            table.add_column("Capabilities", style="green")
            table.add_column("Description", style="white")
            for prov_id, prov_info in MODEL_PROVIDERS.items():
                for m, desc in prov_info["models"].items():
                    marker = " ◀" if m == current else ""
                    prov_name = prov_info["name"].get(_get_lang(), prov_id)
                    caps = ", ".join(sorted(MODEL_CAPABILITIES.get(m, {"text"}))) 
                    table.add_row(prov_name, m + marker, caps, model_desc(m))
            console.print(table)
            # Show multi-model configuration
            mm_model = full_cfg.get("multimodalModel", "")
            backup = full_cfg.get("backupModel", "")
            mm_backup = full_cfg.get("backupMultimodalModel", "")
            img_model = full_cfg.get("imageGenModel", "cogview-3-flash")
            vid_model = full_cfg.get("videoGenModel", "cogvideox-flash")
            sub_models = full_cfg.get("subagentModels", {})
            console.print(f"\n[bold]Multi-Model Configuration:[/bold]")
            console.print(f"  🤖 Main: [cyan]{current}[/cyan] {'(vision ✅)' if is_multimodal_model(current) else '(text only)'}")
            if mm_model:
                console.print(f"  👁  Multimodal: [cyan]{mm_model}[/cyan]")
            if backup:
                console.print(f"  🔄 Backup: [cyan]{backup}[/cyan]")
            if mm_backup:
                console.print(f"  👁🔄 MM Backup: [cyan]{mm_backup}[/cyan]")
            console.print(f"  🎨 Image Gen: [cyan]{img_model}[/cyan]")
            console.print(f"  🎬 Video Gen: [cyan]{vid_model}[/cyan]")
            if sub_models:
                for name, mid in sub_models.items():
                    caps = ", ".join(sorted(get_model_capabilities(mid))) or "text"
                    console.print(f"  🤖 Sub '{name}': [cyan]{mid}[/cyan] ({caps})")
            console.print(f"\n[dim]Usage: /model <model> | /model mm <model> | /model backup <model> | /model sub <name> <model>[/dim]")
            console.print(f"[dim]       任意自定义模型名可直接使用；多Key用 /key 配置 | 思维链保留用 /keepthink、/thinkmodel[/dim]")

    elif command in ("/provider", "/providers"):
        _lang = _get_lang()
        if arg:
            # /provider <id> — switch to a provider (sets baseUrl + default model)
            prov_id = arg.strip().lower()
            if prov_id in MODEL_PROVIDERS:
                prov_info = MODEL_PROVIDERS[prov_id]
                cfg = load_full_config()
                cfg["baseUrl"] = prov_info["baseUrl"]
                # Set first model as default
                first_model = list(prov_info["models"].keys())[0]
                if cfg.get("model", "") not in prov_info["models"]:
                    cfg["model"] = first_model
                save_config(cfg)
                prov_name = prov_info["name"].get(_lang, prov_id)
                console.print(f"[success]{t('provider.selected', name=prov_name, model=cfg['model'])}[/success]")
                console.print(f"[dim]{t('provider.auto_baseUrl', url=prov_info['baseUrl'])}[/dim]")
            else:
                console.print(f"[warning]Unknown provider: {arg}[/warning]")
                console.print(f"Available: {', '.join(MODEL_PROVIDERS.keys())}")
        else:
            # Show all providers
            current_model = load_zai_config().get("model", "") if load_zai_config() else ""
            current_prov_id, _ = find_provider_for_model(current_model)
            table = Table(title=t("provider.title"))
            table.add_column("ID", style="cyan")
            table.add_column("Provider", style="yellow")
            table.add_column("BaseUrl", style="dim")
            table.add_column("Models", style="white")
            for prov_id, prov_info in MODEL_PROVIDERS.items():
                marker = " ◀" if prov_id == current_prov_id else ""
                prov_name = prov_info["name"].get(_lang, prov_id)
                model_list = ", ".join(list(prov_info["models"].keys())[:3])
                if len(prov_info["models"]) > 3:
                    model_list += f" +{len(prov_info['models'])-3}"
                table.add_row(prov_id + marker, prov_name, prov_info["baseUrl"][:40] + "...", model_list)
            console.print(table)

    elif command in ("/config", "/c"):
        handle_config_command(arg, conv)

    elif command in ("/perm", "/permissions", "/p"):
        handle_perm_command(arg)

    elif command in ("/lang",):
        if arg in ("zh", "en"):
            cfg = load_full_config()
            cfg["lang"] = arg
            save_config(cfg)
            console.print(f"[success]{t('config.lang_changed', lang=arg)}[/success]")
        else:
            console.print("Usage: /lang zh|en")

    elif command in ("/thinking", "/think"):
        if arg == "on":
            set_thinking_mode("on")
            console.print(f"[success]{t('thinking.on')}[/success]")
        elif arg == "off":
            set_thinking_mode("off")
            console.print(f"[success]{t('thinking.off')}[/success]")
        else:
            # Show current thinking mode + retention status
            current = get_thinking_mode()
            mode_label = t("thinking.on") if current == "on" else t("thinking.off")
            console.print(f"{t('thinking.current')}: {mode_label}")
            keep_label = t("thinkkeep.status_on") if is_keep_thinking_enabled() else t("thinkkeep.status_off")
            console.print(f"🧠 keepThinking: {keep_label} | {t('thinkkeep.model')}: {get_thinking_compress_model()}")

    elif command in ("/keepthink", "/ktk"):
        # Chain-of-thought retention switch
        if arg == "on":
            set_keep_thinking(True)
            console.print(f"[success]{t('thinkkeep.on', trig=get_thinking_compress_trigger_tokens(), force=get_thinking_single_round_force_tokens())}[/success]")
        elif arg == "off":
            set_keep_thinking(False)
            console.print(f"[success]{t('thinkkeep.off')}[/success]")
        else:
            keep_label = t("thinkkeep.status_on") if is_keep_thinking_enabled() else t("thinkkeep.status_off")
            console.print(f"🧠 keepThinking: {keep_label}")
            console.print(f"   {t('thinkkeep.model')}: {get_thinking_compress_model()}")
            console.print(f"[dim]{get_thinking_compress_trigger_tokens()} tok → 后台压缩（不阻塞）; 单轮 {get_thinking_single_round_force_tokens()} tok → 强制停止+同步压缩[/dim]")
            console.print(f"[dim]{t('thinkkeep.usage')}[/dim]")

    elif command in ("/thought", "/thoughts", "/td"):
        # List the current conversation's retained chain-of-thought
        if not is_keep_thinking_enabled():
            console.print(f"[warning]{t('thought.disabled')}[/warning]")
        elif conv is None:
            console.print(f"[dim]{t('thought.empty')}[/dim]")
        else:
            summary = (getattr(conv, "thinking_summary", "") or "").strip()
            entries = list(getattr(conv, "thinking_archive", []) or [])
            if not summary and not entries:
                console.print(f"[dim]{t('thought.empty')}[/dim]")
            else:
                _tt = Table(title=f"🧠 {t('thought.title')}")
                _tt.add_column("#", style="dim", justify="right")
                _tt.add_column(t("thought.pending"), style="white", overflow="fold")
                _tt.add_column("tok", style="yellow", justify="right")
                if summary:
                    _sum_prev = summary[:150].replace("\n", " ") + ("…" if len(summary) > 150 else "")
                    _tt.add_row("S", f"[cyan]{t('thought.summary')}[/cyan] {_sum_prev}", str(_estimate_tokens(summary)))
                for i, e in enumerate(entries, 1):
                    _txt = (e.get("text") or "").strip()
                    _prev = _txt[:150].replace("\n", " ") + ("…" if len(_txt) > 150 else "")
                    if e.get("compressing"):
                        _prev += " ⏳"
                    _tt.add_row(str(i), _prev, str(int(e.get("tokens", 0) or 0)))
                console.print(_tt)
                console.print(
                    f"[dim]{len(entries)} 条 | {t('thought.pending')} {conv.thinking_pending_tokens()} tok "
                    f"/ 触发 {get_thinking_compress_trigger_tokens()} tok | 摘要 {_estimate_tokens(summary)} tok[/dim]"
                )

    elif command in ("/quiet", "/hidetools", "/qt"):
        # Hide tool work output: spinner + hyperlink, only final answer in TUI
        if arg == "on":
            set_quiet_tools(True)
            console.print(f"[success]{t('quiet.on')}[/success]")
        elif arg == "off":
            set_quiet_tools(False)
            console.print(f"[success]{t('quiet.off')}[/success]")
        else:
            label = t("quiet.status_on") if is_quiet_tools_enabled() else t("quiet.status_off")
            console.print(f"🔇 hideWorkOutput: {label}")
            console.print("[dim]Usage: /quiet on|off[/dim]")

    elif command in ("/thinkmodel", "/tkm"):
        # Model used to compress accumulated chain-of-thought (any model name OK)
        if not arg:
            console.print(f"{t('thinkkeep.model')}: {get_thinking_compress_model()}")
            console.print(f"[dim]Usage: /thinkmodel <model>[/dim]")
        else:
            mname = arg.strip()
            set_thinking_compress_model(mname)
            if mname not in AVAILABLE_MODELS:
                console.print(f"[dim]↳ 自定义模型（将使用匹配的 provider/model Key 调用）: {mname}[/dim]")
            console.print(f"[success]{t('thinkmodel.set', model=mname)}[/success]")

    elif command in ("/key", "/keys", "/apikey"):
        # Multi API-key management:
        #   /key                              list masked keys
        #   /key <providerId|modelName> <k>   set a dedicated key
        #   /key del <providerId|modelName>   remove it (falls back to default)
        cfg = load_full_config()
        provider_keys = cfg.get("providerKeys") or {}
        model_keys = cfg.get("modelKeys") or {}
        sub_parts = arg.split(None, 1)
        if not arg:
            table = Table(title=t("key.list_title"))
            table.add_column("Scope", style="yellow")
            table.add_column("Target", style="cyan")
            table.add_column("API Key (masked)", style="white")
            _mask = lambda k: (str(k)[:6] + "..." + str(k)[-4:]) if k and len(str(k)) > 12 else ("***" if k else "")
            table.add_row("default", t("key.default"), _mask(cfg.get("apiKey", "")))
            for pid, k in sorted(provider_keys.items()):
                table.add_row("provider", pid, _mask(k))
            for mname, k in sorted(model_keys.items()):
                table.add_row("model", mname, _mask(k))
            console.print(table)
            console.print(f"[dim]{t('key.usage')}[/dim]")
            console.print(f"[dim]provider ID: {', '.join(MODEL_PROVIDERS.keys())}[/dim]")
        elif sub_parts[0] == "del":
            target = sub_parts[1].strip() if len(sub_parts) > 1 else ""
            if target in provider_keys:
                del provider_keys[target]
                cfg["providerKeys"] = provider_keys
                save_config(cfg)
                console.print(f"[success]{t('key.deleted', target=target)}[/success]")
            elif target in model_keys:
                del model_keys[target]
                cfg["modelKeys"] = model_keys
                save_config(cfg)
                console.print(f"[success]{t('key.deleted', target=target)}[/success]")
            else:
                console.print(f"[warning]{t('key.not_found', target=target)}[/warning]")
        elif len(sub_parts) == 2:
            target, key_value = sub_parts[0].strip(), sub_parts[1].strip()
            if not key_value:
                console.print(f"[warning]{t('key.bad_format')}[/warning]")
            elif target in MODEL_PROVIDERS:
                cfg.setdefault("providerKeys", {})[target] = key_value
                save_config(cfg)
                console.print(f"[success]{t('key.set_provider', target=target)}[/success]")
            else:
                # Treat as an exact model name (registered OR custom — both work)
                cfg.setdefault("modelKeys", {})[target] = key_value
                save_config(cfg)
                if target not in AVAILABLE_MODELS:
                    console.print(f"[dim]↳ 未内置注册的模型名，将作为精确模型 Key 使用: {target}[/dim]")
                console.print(f"[success]{t('key.set_model', target=target)}[/success]")
        else:
            console.print(f"[warning]{t('key.bad_format')}[/warning]")

    elif command in ("/compress", "/compact"):
        # Manual context compression (all numbers shown in tokens since 26w35a)
        console.print(f"[info]{t('context.manual')}[/info]")
        old_count = len(conv.messages)
        old_tokens = _estimate_messages_tokens(conv.messages)
        compressed = compress_context(conv.messages, force_llm_summary=True)
        conv.messages = compressed
        conv.save()
        new_count = len(conv.messages)
        new_tokens = _estimate_messages_tokens(conv.messages)
        console.print(f"[success]{t('context.manual_done_tok', tokens=new_tokens)}[/success]")
        console.print(f"[dim]  ~{old_tokens} → ~{new_tokens} tokens | {old_count} → {new_count} msgs[/dim]")

    elif command in ("/search", "/web", "/websearch"):
        if arg:
            console.print(f"[info]{t('tool.web_search')}: {arg}[/info]")
            results = execute_tool("web_search", {"query": arg})
            console.print(Panel(results, title=f"🔍 {t('tool.web_search_result')}", border_style="cyan", padding=(1, 2)))
        else:
            console.print("[warning]Usage: /search <query>[/warning]")

    elif command in ("/logs", "/log"):
        # List thinking logs, compression reports, sub-agent logs, work logs
        _lang = _get_lang()
        thinking_logs = sorted(THINKING_LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        compress_reports = sorted(THINKING_LOG_DIR.glob("thinkcompress_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        subagent_logs = sorted(SUBAGENT_LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        work_logs = sorted(WORK_LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)

        if not thinking_logs and not subagent_logs and not compress_reports and not work_logs:
            console.print("[dim]暂无日志[/dim]" if _lang == "zh" else "[dim]No logs yet[/dim]")
            return True

        def _log_line(log, title, icon="📄"):
            launcher = _create_log_launcher(log, title=title)
            link = _make_clickable_link(launcher, label=f"{icon} {log.name}")
            mtime = datetime.fromtimestamp(log.stat().st_mtime).strftime("%H:%M:%S")
            size = log.stat().st_size
            console.print(f"  {link} [{mtime}] ({size}B)")

        if compress_reports:
            console.print(f"\n[bold]🧠 {'思维压缩报告（原文 vs 摘要）' if _lang == 'zh' else 'Thinking Compression Reports'}:[/bold]")
            for log in compress_reports[:10]:
                _log_line(log, "Thinking compression report", "🧠")

        if thinking_logs:
            console.print(f"\n[bold]💭 {'思考日志' if _lang == 'zh' else 'Thinking Logs'}:[/bold]")
            for log in thinking_logs[:10]:
                _log_line(log, "Thinking Log")

        if work_logs:
            console.print(f"\n[bold]🛠 {'工作输出日志（/quiet 模式）' if _lang == 'zh' else 'Work Output Logs (quiet mode)'}:[/bold]")
            for log in work_logs[:10]:
                _log_line(log, "Work output", "🛠")

        if subagent_logs:
            console.print(f"\n[bold]🤖 {'子代理日志' if _lang == 'zh' else 'Sub-agent Logs'}:[/bold]")
            for log in subagent_logs[:10]:
                _log_line(log, "Sub-agent Log", "🤖")

        console.print(f"\n[dim]{'Ctrl+点击链接在新终端查看 | 日志根目录' if _lang == 'zh' else 'Ctrl+Click link to view in new terminal | Log root'}: {ZAGENT_HOME}[/dim]")

    elif command in ("/quit", "/q", "/exit"):
        conv.save()
        console.print(f"[success]{t('cmd.exit_save')}[/success]")
        return False

    else:
        console.print(f"[warning]{t('cmd.unknown', cmd=command)}[/warning]")

    return True

def handle_perm_command(arg):
    """Handle /perm subcommands for permission management.
    Usage:
      /perm                - 显示所有权限
      /perm <name> on      - 开启权限
      /perm <name> off     - 关闭权限
      /perm <name> <数字>  - 设置数值
      /perm <name> null    - 设为无限制
      /perm reset          - 重置为默认
      /perm file           - 显示权限文件路径
    """
    _lang = _get_lang()
    def _perm_name(key):
        """Get localized permission name."""
        info = PERM_NAMES.get(key, {})
        name = info.get(_lang, info.get("zh", key))
        return f"{name} ({key})"

    def _perm_val_display(v):
        """Display a permission value with localization."""
        if v is None:
            return t("perm.unlimited")
        if isinstance(v, bool):
            return "✅" if v else "❌"
        if isinstance(v, (int, float)):
            return f"{v:,}"
        if isinstance(v, list):
            return ", ".join(str(x) for x in v) if v else t("perm.empty")
        return str(v)

    # /perm reset
    if arg == "reset":
        if PERM_CONFIG.exists():
            PERM_CONFIG.unlink()
        console.print(f"[success]{t('perm.reset_done')}[/success]")
        return

    # /perm file
    if arg == "file":
        console.print(f"{t('perm.file')}: {PERM_CONFIG}")
        return

    # /perm <name> <value> — set a permission
    parts = arg.strip().split(None, 1)
    if len(parts) == 2:
        key, val_str = parts[0].strip(), parts[1].strip()
        # Load current custom perms
        custom = load_permissions()
        perms = dict(DEFAULT_PERMISSIONS)
        perms.update(custom)

        if val_str.lower() == "on":
            custom[key] = True
        elif val_str.lower() == "off":
            custom[key] = False
        elif val_str.lower() == "null":
            custom[key] = None
        else:
            try:
                custom[key] = int(val_str)
            except ValueError:
                console.print(f"[warning]{t('perm.edit_invalid')}[/warning]")
                return

        save_permissions(custom)
        # Show the updated value
        new_val = custom[key]
        console.print(f"[success]{_perm_name(key)}: {_perm_val_display(new_val)}[/success]")
        return

    # /perm <name> — show single permission
    if len(parts) == 1 and parts[0]:
        key = parts[0]
        perms = get_permissions()
        if key in perms:
            console.print(f"{_perm_name(key)}: {_perm_val_display(perms[key])}")
        else:
            console.print(f"[warning]{key}: {t('perm.edit_invalid')}[/warning]")
        return

    # /perm (no args) — show all permissions
    perms = get_permissions()
    bool_perms = {k: v for k, v in perms.items() if isinstance(v, bool)}
    num_perms = {k: v for k, v in perms.items() if isinstance(v, (int, float, type(None))) and not isinstance(v, bool)}
    list_perms = {k: v for k, v in perms.items() if isinstance(v, list)}

    content_lines = []
    content_lines.append(f"[bold]{t('perm.bool_section')}:[/bold]")
    for k, v in bool_perms.items():
        icon = "✅" if v else "❌"
        content_lines.append(f"  {icon} {_perm_name(k)}")
    content_lines.append("")
    content_lines.append(f"[bold]{t('perm.num_section')}:[/bold]")
    for k, v in num_perms.items():
        val_display = _perm_val_display(v)
        content_lines.append(f"  🔢 {_perm_name(k)}: {val_display}")
    if any(v for v in list_perms.values()):
        content_lines.append("")
        content_lines.append(f"[bold]{t('perm.list_section')}:[/bold]")
        for k, v in list_perms.items():
            if v:
                content_lines.append(f"  📝 {_perm_name(k)}: {v}")
            else:
                content_lines.append(f"  ⬜ {_perm_name(k)} {t('perm.empty')}")

    console.print(Panel(
        "\n".join(content_lines),
        title=t("perm.title"),
        border_style="yellow"
    ))
    console.print(f"\n[dim]{t('perm.file')}: {PERM_CONFIG}[/dim]")
    console.print(f"  [dim]/perm allowWrite on    - {t('perm.set_on')}[/dim]")
    console.print(f"  [dim]/perm allowExecute off  - {t('perm.set_off')}[/dim]")
    console.print(f"  [dim]/perm confirmDangerousCommands off - {t('perm.set_off')} ({PERM_NAMES['confirmDangerousCommands'].get(_lang, '⚠')})[/dim]")
    console.print(f"  [dim]/perm maxFileReadSize 100000 - {t('perm.set_num')}[/dim]")
    console.print(f"  [dim]/perm maxFileReadSize null   - {t('perm.set_null')}[/dim]")
    console.print(f"  [dim]/perm reset - {t('perm.reset')}[/dim]")
    console.print(f"  [dim]/perm file  - {t('perm.file')}[/dim]")

def handle_config_command(arg, conv):
    """Handle /config subcommands."""
    if arg == "setup":
        run_setup_wizard()
    elif arg == "test":
        test_api_connection()
    elif arg == "path":
        console.print(f"[bold]{t('config.search_order')}:[/bold]")
        for i, p in enumerate(ZAI_CONFIG_PATHS):
            status = ""
            if p == LOCAL_CONFIG:
                status = f" ← {t('config.local_config')}"
            exists = "✅" if p.exists() else "❌"
            console.print(f"  {i+1}. {exists} {p}{status}")
    elif arg == "models":
        _lang = _get_lang()
        table = Table(title=t("config.available_models"))
        table.add_column("Provider", style="yellow")
        table.add_column("Model", style="cyan")
        table.add_column("Description", style="white")
        for prov_id, prov_info in MODEL_PROVIDERS.items():
            for m, desc in prov_info["models"].items():
                prov_name = prov_info["name"].get(_lang, prov_id)
                table.add_row(prov_name, m, model_desc(m))
        console.print(table)
    elif arg == "permissions":
        handle_perm_command("")
    elif arg == "prompt":
        sp = get_system_prompt()
        console.print(Panel(sp[:1000] + ("..." if len(sp) > 1000 else ""), title=t("cmd.sys_prompt"), border_style="blue"))
    else:
        config = load_zai_config()
        if config:
            safe_config = {k: (v[:8] + "..." if k == "apiKey" and len(str(v)) > 8 else v) for k, v in config.items()}
            console.print(Panel(
                json.dumps(safe_config, ensure_ascii=False, indent=2),
                title=f"⚙️ {t('config.title')}",
                border_style="cyan"
            ))
        else:
            console.print(f"[warning]{t('config.no_config')}[/warning]")
        console.print(f"\n[dim]{t('config.or_edit')}: {LOCAL_CONFIG}[/dim]")
        console.print(f"  [dim]/config setup    - {t('config.setup')}[/dim]")
        console.print(f"  [dim]/config test     - {t('config.test')}[/dim]")
        console.print(f"  [dim]/config path     - {t('config.path')}[/dim]")
        console.print(f"  [dim]/config models   - {t('config.models')}[/dim]")
        console.print(f"  [dim]/model           - Show/set models (main, mm, backup, sub)[/dim]")
        console.print(f"  [dim]/perm            - {t('perm.title')}[/dim]")
        console.print(f"  [dim]/perm allowWrite on/off - {t('perm.set_on')}/{t('perm.set_off')}[/dim]")
        console.print(f"  [dim]/config prompt   - Show system prompt[/dim]")

def run_setup_wizard():
    """Interactive configuration wizard with provider selection and multi-model setup."""
    _lang = _get_lang()
    console.print(f"\n[bold]{t('config.setup_title')}[/bold]")
    console.print(f"{t('config.will_save')}: {LOCAL_CONFIG}\n")

    cfg = load_full_config()

    # ── Step 1: Select provider ──────────────────────────────
    console.print(f"[bold]{t('provider.select')}:[/bold]")
    provider_keys = list(MODEL_PROVIDERS.keys())
    for i, pk in enumerate(provider_keys, 1):
        prov = MODEL_PROVIDERS[pk]
        prov_name = prov["name"].get(_lang, pk)
        console.print(f"  {i}. {prov_name} ({pk})")
    console.print(f"  0. {t('provider.custom')}")

    try:
        choice = input(f"  > ").strip()
    except (EOFError, KeyboardInterrupt):
        return

    base_url = ""
    default_model = ""
    if choice == "0" or choice.lower() == "custom":
        base_url = input(f"{t('config.api_url')}: ").strip()
        if not base_url:
            console.print(f"[danger]{t('config.api_key_required')}[/danger]")
            return
    else:
        try:
            idx = int(choice) - 1
            selected_prov = provider_keys[idx]
        except (ValueError, IndexError):
            selected_prov = provider_keys[0]
        prov_info = MODEL_PROVIDERS[selected_prov]
        base_url = prov_info["baseUrl"]
        prov_name = prov_info["name"].get(_lang, selected_prov)
        console.print(f"  [dim]{t('provider.auto_baseUrl', url=base_url)}[/dim]")

    # ── Step 2: API Key ──────────────────────────────────────
    api_key = input(f"{t('config.api_key')}: ").strip()
    if not api_key:
        console.print(f"[danger]{t('config.api_key_required')}[/danger]")
        return

    cfg.update({"baseUrl": base_url, "apiKey": api_key})

    # ── Step 3: Main Agent Model (required) ──────────────────
    console.print(f"\n[bold cyan]── 主Agent模型 (Main Agent Model) ──[/bold cyan]" if _lang == "zh" else f"\n[bold cyan]── Main Agent Model ──[/bold cyan]")
    _select_model_from_all(cfg, "model", "主Agent模型" if _lang == "zh" else "Main agent model", required=True, default_model=cfg.get("model", ""))

    # ── Step 4: Multimodal/Vision Model (optional) ───────────
    main_model = cfg.get("model", "")
    if is_multimodal_model(main_model):
        console.print(f"\n[green]  ✅ {'主模型已支持视觉，无需额外配置多模态模型' if _lang == 'zh' else 'Main model supports vision, no separate multimodal model needed'}[/green]")
    else:
        console.print(f"\n[bold cyan]── {'多模态/视觉模型 (可选)' if _lang == 'zh' else 'Multimodal/Vision Model (optional)'} ──[/bold cyan]")
        console.print(f"  [dim]{'主模型不支持图片理解，配置一个多模态模型用于image_view工具' if _lang == 'zh' else 'Main model does not support image understanding. Configure a multimodal model for image_view tool'}[/dim]")
        _select_model_from_all(cfg, "multimodalModel", "多模态模型" if _lang == "zh" else "Multimodal model", required=False)

    # ── Step 5: Backup Main Model (optional) ─────────────────
    console.print(f"\n[bold cyan]── {'备用主Agent模型 (可选)' if _lang == 'zh' else 'Backup Main Model (optional)'} ──[/bold cyan]")
    console.print(f"  [dim]{'主模型429限流或出错时自动切换的备用模型' if _lang == 'zh' else 'Backup model to auto-switch when main model gets 429 or errors'}[/dim]")
    _select_model_from_all(cfg, "backupModel", "备用主模型" if _lang == "zh" else "Backup main model", required=False)

    # ── Step 6: Backup Multimodal Model (optional) ───────────
    if cfg.get("multimodalModel"):
        console.print(f"\n[bold cyan]── {'备用多模态模型 (可选)' if _lang == 'zh' else 'Backup Multimodal Model (optional)'} ──[/bold cyan]")
        console.print(f"  [dim]{'多模态模型429限流或出错时自动切换的备用模型' if _lang == 'zh' else 'Backup model for when multimodal model gets 429 or errors'}[/dim]")
        _select_model_from_all(cfg, "backupMultimodalModel", "备用多模态模型" if _lang == "zh" else "Backup multimodal model", required=False)

    # ── Step 7: Sub-Agent Models ─────────────────────────────
    console.print(f"\n[bold cyan]── {'子代理模型 (可选, 可添加多个)' if _lang == 'zh' else 'Sub-Agent Models (optional, can add multiple)'} ──[/bold cyan]")
    console.print(f"  [dim]{'子代理可以使用图片生成、视频生成等特殊模型' if _lang == 'zh' else 'Sub-agents can use special models like image/video generation'}[/dim]")
    subagent_models = cfg.get("subagentModels", {})
    while True:
        if subagent_models:
            console.print(f"  [dim]{'已配置的子代理模型:' if _lang == 'zh' else 'Configured sub-agent models:'}[/dim]")
            for name, mid in subagent_models.items():
                caps = ", ".join(sorted(MODEL_CAPABILITIES.get(mid, set()))) or "text"
                console.print(f"    🤖 {name}: {mid} ({caps})")
        prompt_text = f"  {'添加子代理模型? 输入名称和模型(如: imager cogview-3-flash), 回车跳过' if _lang == 'zh' else 'Add sub-agent model? Enter name and model (e.g.: imager cogview-3-flash), Enter to skip'}: "
        try:
            sub_input = input(prompt_text).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not sub_input:
            break
        parts = sub_input.split(None, 1)
        if len(parts) == 2:
            sub_name, sub_model = parts
            # Any model name accepted (registered or custom)
            subagent_models[sub_name] = sub_model
            caps = ", ".join(sorted(get_model_capabilities(sub_model))) or "text"
            _custom = "" if sub_model in AVAILABLE_MODELS else " (custom)"
            console.print(f"  [success]✅ {'已添加' if _lang == 'zh' else 'Added'}: {sub_name} = {sub_model} ({caps}){_custom}[/success]")
        else:
            console.print(f"  [warning]⚠️ {'格式: 名称 模型ID' if _lang == 'zh' else 'Format: name model_id'}[/warning]")
    if subagent_models:
        cfg["subagentModels"] = subagent_models

    # ── Step 8: Image Generation Model (optional) ────────────
    console.print(f"\n[bold cyan]── {'图片生成模型 (可选, 子代理使用)' if _lang == 'zh' else 'Image Generation Model (optional, for sub-agents)'} ──[/bold cyan]")
    console.print(f"  [dim]{'默认使用 cogview-3-flash' if _lang == 'zh' else 'Default: cogview-3-flash'}[/dim]")
    _select_model_from_all(cfg, "imageGenModel", "图片生成模型" if _lang == "zh" else "Image gen model", required=False, default_model=cfg.get("imageGenModel", "cogview-3-flash"))

    # ── Step 9: Video Generation Model (optional) ────────────
    console.print(f"\n[bold cyan]── {'视频生成模型 (可选, 子代理使用)' if _lang == 'zh' else 'Video Generation Model (optional, for sub-agents)'} ──[/bold cyan]")
    console.print(f"  [dim]{'默认使用 cogvideox-flash' if _lang == 'zh' else 'Default: cogvideox-flash'}[/dim]")
    _select_model_from_all(cfg, "videoGenModel", "视频生成模型" if _lang == "zh" else "Video gen model", required=False, default_model=cfg.get("videoGenModel", "cogvideox-flash"))

    # ── Step 10: Extra API keys for other providers / models ──
    console.print(f"\n[bold cyan]── {'多 API Key (可选, 为其他服务商/模型配置独立Key)' if _lang == 'zh' else 'Extra API Keys (optional, dedicated keys for providers/models)'} ──[/bold cyan]")
    console.print(f"  [dim]{'服务商ID: ' + ', '.join(MODEL_PROVIDERS.keys()) if _lang == 'zh' else 'Provider IDs: ' + ', '.join(MODEL_PROVIDERS.keys())}[/dim]")
    while True:
        try:
            key_input = input(f"  {'输入 <服务商ID或模型名> <apiKey>，回车跳过' if _lang == 'zh' else 'Enter <providerId|modelName> <apiKey>, Enter to skip'}: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not key_input:
            break
        kp = key_input.split(None, 1)
        if len(kp) != 2:
            console.print(f"  [warning]⚠️ {'格式错误' if _lang == 'zh' else 'Bad format'}[/warning]")
            continue
        ktarget, kvalue = kp
        if ktarget in MODEL_PROVIDERS:
            cfg.setdefault("providerKeys", {})[ktarget] = kvalue
            console.print(f"  [success]✅ provider {ktarget} key[/success]")
        else:
            cfg.setdefault("modelKeys", {})[ktarget] = kvalue
            console.print(f"  [success]✅ model {ktarget} key[/success]")

    # ── Step 11: Chain-of-thought retention ───────────────────
    console.print(f"\n[bold cyan]── {'思维链保留 (可选)' if _lang == 'zh' else 'Chain-of-thought Retention (optional)'} ──[/bold cyan]")
    console.print(f"  [dim]{'开启后AI每轮思考会保留并注入下一轮；累计10000 tokens后台压缩，单轮30000 tokens强制压缩' if _lang == 'zh' else 'When ON, reasoning is retained/injected across rounds; background compress at 10000 tokens, force-stop at 30000 tokens per round'}[/dim]")
    try:
        keep_in = input(f"  {'开启思维链保留? (y/N): ' if _lang == 'zh' else 'Enable chain-of-thought retention? (y/N): '}").strip().lower()
    except (EOFError, KeyboardInterrupt):
        keep_in = ""
    cfg["keepThinking"] = keep_in in ("y", "yes", "on", "1", "是", "开")
    if cfg["keepThinking"]:
        _select_model_from_all(
            cfg, "thinkingCompressModel",
            "思维压缩模型" if _lang == "zh" else "Thinking compression model",
            required=False, default_model=cfg.get("thinkingCompressModel", cfg.get("model", "glm-4-flash")),
        )

    # Save configuration
    save_config(cfg)
    console.print(f"\n[success]{t('config.saved', path=LOCAL_CONFIG)}[/success]")

    # Test connection
    console.print(f"[dim]{t('config.conn_test')}[/dim]")
    err = test_api_connection_silent()
    if err:
        console.print(f"[warning]{t('config.conn_fail', err=err)}[/warning]")
    else:
        console.print(f"[success]{t('config.conn_ok')}[/success]")

    # Show configuration summary
    console.print(f"\n[bold]{'配置摘要' if _lang == 'zh' else 'Configuration Summary'}:[/bold]")
    console.print(f"  🤖 {'主模型' if _lang == 'zh' else 'Main'}: {cfg.get('model', '?')}")
    mm = cfg.get("multimodalModel", "")
    if mm:
        console.print(f"  👁  {'多模态' if _lang == 'zh' else 'MM'}: {mm}")
    elif is_multimodal_model(cfg.get("model", "")):
        console.print(f"  👁  {'多模态' if _lang == 'zh' else 'MM'}: (主模型已支持 / main supports vision)")
    bk = cfg.get("backupModel", "")
    if bk:
        console.print(f"  🔄 {'备用主模型' if _lang == 'zh' else 'Backup'}: {bk}")
    mm_bk = cfg.get("backupMultimodalModel", "")
    if mm_bk:
        console.print(f"  👁🔄 {'备用多模态' if _lang == 'zh' else 'MM Backup'}: {mm_bk}")
    for name, mid in subagent_models.items():
        caps = ", ".join(sorted(get_model_capabilities(mid))) or "text"
        console.print(f"  🤖 {'子代理' if _lang == 'zh' else 'Sub'} '{name}': {mid} ({caps})")
    img = cfg.get("imageGenModel", "")
    if img:
        console.print(f"  🎨 {'图片生成' if _lang == 'zh' else 'Image Gen'}: {img}")
    vid = cfg.get("videoGenModel", "")
    if vid:
        console.print(f"  🎬 {'视频生成' if _lang == 'zh' else 'Video Gen'}: {vid}")
    if cfg.get("keepThinking"):
        console.print(f"  🧠 {'思维链保留' if _lang == 'zh' else 'Keep thinking'}: ON ({cfg.get('thinkingCompressModel') or cfg.get('model')})")
    if cfg.get("providerKeys") or cfg.get("modelKeys"):
        n_extra = len(cfg.get("providerKeys") or {}) + len(cfg.get("modelKeys") or {})
        console.print(f"  🔑 {'额外API Key' if _lang == 'zh' else 'Extra API keys'}: {n_extra}")


def _select_model_from_all(cfg, cfg_key, label, required=False, default_model=""):
    """Helper for setup wizard: let user select a model from all available models.
    Updates cfg[cfg_key] with the selected model."""
    _lang = _get_lang()
    # Show models grouped by provider
    console.print(f"  [bold]{label}:[/bold]")
    all_models = []
    for prov_id, prov_info in MODEL_PROVIDERS.items():
        prov_name = prov_info["name"].get(_lang, prov_id)
        for m, desc in prov_info["models"].items():
            caps = ", ".join(sorted(MODEL_CAPABILITIES.get(m, set()))) or "text"
            m_desc = desc.get(_lang, desc.get("zh", m)) if isinstance(desc, dict) else str(desc)
            all_models.append((m, prov_name, caps, m_desc))
    
    for i, (m, prov_name, caps, m_desc) in enumerate(all_models, 1):
        marker = " ◀" if m == default_model else ""
        console.print(f"    {i}. {m}{marker} [{caps}] ({prov_name})")
    
    optional_hint = "" if required else (f" ({'回车跳过' if _lang == 'zh' else 'Enter to skip'})" )
    try:
        mchoice = input(f"  > {optional_hint}: ").strip()
    except (EOFError, KeyboardInterrupt):
        mchoice = ""
    
    if not mchoice:
        if required:
            if default_model:
                cfg[cfg_key] = default_model
            else:
                cfg[cfg_key] = all_models[0][0] if all_models else "glm-4-flash"
        # optional and empty: don't set or clear existing
        return
    
    # Try as number index
    try:
        idx = int(mchoice) - 1
        if 0 <= idx < len(all_models):
            cfg[cfg_key] = all_models[idx][0]
            console.print(f"  [success]✅ {label}: {all_models[idx][0]}[/success]")
            return
    except ValueError:
        pass
    
    # Try as model name
    if mchoice in AVAILABLE_MODELS:
        cfg[cfg_key] = mchoice
        console.print(f"  [success]✅ {label}: {mchoice}[/success]")
        return
    
    # Try partial match
    matches = [m for m, _, _, _ in all_models if mchoice.lower() in m.lower()]
    if len(matches) == 1:
        cfg[cfg_key] = matches[0]
        console.print(f"  [success]✅ {label}: {matches[0]}[/success]")
        return
    elif len(matches) > 1:
        console.print(f"  [warning]⚠️ {'多个匹配' if _lang == 'zh' else 'Multiple matches'}: {', '.join(matches[:5])}[/warning]")
        # Multiple matches: do NOT fall through to treating the number as a custom name
        if required:
            cfg[cfg_key] = default_model or (all_models[0][0] if all_models else "glm-4-flash")
        return

    # Anything else: accept it as a CUSTOM model name — any OpenAI-compatible
    # model works as long as the endpoint + key are configured.
    cfg[cfg_key] = mchoice
    console.print(f"  [success]✅ {label}: {mchoice}[/success]")
    console.print(f"  [dim]↳ {'自定义模型（将使用当前 baseUrl + 匹配的 Key 调用）' if _lang == 'zh' else 'Custom model (called via current baseUrl + matching key)'}[/dim]")

def test_api_connection():
    """Test API connection and display result."""
    console.print(f"[dim]{t('config.conn_test')}[/dim]")
    err = test_api_connection_silent()
    if err:
        console.print(f"[danger]{t('config.conn_fail', err=err)}[/danger]")
    else:
        console.print(f"[success]{t('config.conn_ok')}[/success]")

def test_api_connection_silent():
    """Test API connection. Returns error string or None."""
    config = load_zai_config()
    if not config:
        return t("config.no_config")
    try:
        import urllib.request
        url = f"{config['baseUrl'].rstrip('/')}/chat/completions"
        body = json.dumps({
            "model": config.get("model", "glm-4-flash"),
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 5,
        }).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config['apiKey']}",
        }
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return None
    except Exception as e:
        return str(e)

def show_help():
    """Display help information."""
    lang = _get_lang()

    # Commands
    table = Table(title=f"📖 {t('help.title')} — {APP_NAME} v{VERSION}")
    table.add_column("Command", style="bold cyan")
    table.add_column("Description", style="white")

    commands = [
        ("/new [name]", t("help.new")),
        ("/list", t("help.list")),
        ("/open", t("help.open")),
        ("/delete", t("help.delete")),
        ("/rename", t("help.rename")),
        ("/export", t("help.export")),
        ("/sys <prompt>", t("help.sys")),
        ("/model [name]", t("help.model")),
        ("/provider [id]", t("help.provider")),
        ("/config", t("help.config")),
        ("/perm", t("perm.title")),
        ("/cd [path]", t("help.cd_desc")),
        ("/cv", t("help.clipboard")),
        ("/lang zh|en", t("help.lang")),
        ("/thinking on|off", t("help.thinking")),
        ("/keepthink on|off", "保留思维链并跨轮注入 (压缩阈值=主模型窗口10%/20%)"),
        ("/thought list", "列出当前对话保留的思维链 (需开启 /keepthink)"),
        ("/thinkmodel <model>", "设置思维链压缩模型"),
        ("/quiet on|off", "隐藏工具工作输出（转圈+链接，仅最终回答上屏）"),
        ("/key [target key]", "多API Key管理 (按服务商/模型)"),
        ("/compress", t("help.compress")),
        ("/search <q>", t("help.search_desc")),
        ("/logs", t("help.logs")),
        ("/info", t("help.info")),
        ("/clear", t("help.clear")),
        ("/quit", t("help.quit")),
    ]
    for cmd, desc in commands:
        table.add_row(cmd, desc)
    console.print(table)

    # Coding tools
    table2 = Table(title=f"🔧 {t('help.coding_tools')}")
    table2.add_column("Tool", style="bold magenta")
    table2.add_column("Description", style="white")
    tools = [
        ("bash", t("help.bash_desc")),
        ("read", t("help.read_desc")),
        ("write", t("help.write_desc")),
        ("edit", t("help.edit_desc")),
        ("glob", t("help.glob_desc")),
        ("grep", t("help.grep_desc")),
        ("web_search", t("help.search_desc")),
    ]
    for tool, desc in tools:
        table2.add_row(tool, desc)
    console.print(table2)

    # AI Media tools
    table3 = Table(title="🎨 AI Media Tools")
    table3.add_column("Tool", style="bold magenta")
    table3.add_column("Description", style="white")
    ai_tools = [
        ("image_view", "Analyze/understand images (auto-selects multimodal model)"),
        ("video_view", "Analyze/understand videos (auto-selects video-capable model)"),
        ("image_generate", "Generate AI images from text (CogView-3-Flash)"),
        ("video_generate", "Generate AI videos from text (CogVideoX-Flash)"),
        ("task", "Spawn sub-agent (supports model parameter for specific capabilities)"),
    ]
    for tool, desc in ai_tools:
        table3.add_row(tool, desc)
    console.print(table3)

    # Feature switches — current status + how to toggle
    keep_on = is_keep_thinking_enabled()
    quiet_on = is_quiet_tools_enabled()
    think_mode = get_thinking_mode()
    trig = get_thinking_compress_trigger_tokens()
    force = get_thinking_single_round_force_tokens()
    table4 = Table(title="⚙️ 功能开关")
    table4.add_column("功能", style="bold cyan")
    table4.add_column("当前状态", style="bold yellow")
    table4.add_column("说明", style="white")
    table4.add_column("命令", style="bold green")
    table4.add_row(
        "思维链保留",
        "✅ 开启" if keep_on else "❌ 关闭",
        f"AI 思考跨轮记忆，不丢弃；累计 {trig} tok 后台压缩，单轮 {force} tok 强制压缩\n压缩模型: {get_thinking_compress_model()}",
        "/keepthink on|off",
    )
    table4.add_row(
        "思维链压缩模型",
        get_thinking_compress_model(),
        "负责压缩历史思考的模型，建议用便宜快速的",
        "/thinkmodel <模型名>",
    )
    table4.add_row(
        "思考模式",
        "✅ 强制" if think_mode == "on" else "❌ 自动",
        "强制模型每轮都输出思考过程",
        "/thinking on|off",
    )
    table4.add_row(
        "隐藏工作输出",
        "✅ 开启" if quiet_on else "❌ 关闭",
        "工具执行只显示转圈，输出收进链接，仅最终回答上屏",
        "/quiet on|off",
    )
    console.print(table4)

# ════════════════════════════════════════════════════════════════
# §13 ENTRY POINT
# ════════════════════════════════════════════════════════════════

def main():
    """Main entry point."""
    # Handle CLI args
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        if cmd == "setup":
            run_setup_wizard()
            return
        elif cmd == "config":
            handle_config_command(sys.argv[2] if len(sys.argv) > 2 else "", None)
            return
        elif cmd == "models":
            _lang = _get_lang()
            table = Table(title=t("config.available_models"))
            table.add_column("Provider", style="yellow")
            table.add_column("Model", style="cyan")
            table.add_column("Description", style="white")
            for prov_id, prov_info in MODEL_PROVIDERS.items():
                for m, desc in prov_info["models"].items():
                    prov_name = prov_info["name"].get(_lang, prov_id)
                    table.add_row(prov_name, m, model_desc(m))
            console.print(table)
            return
        elif cmd == "lang" and len(sys.argv) > 2:
            lang = sys.argv[2]
            if lang in ("zh", "en"):
                cfg = load_full_config()
                cfg["lang"] = lang
                save_config(cfg)
                console.print(f"{t('config.lang_changed', lang=lang)}")
            return

    # Print banner
    # Clean up old thinking/sub-agent logs from previous sessions
    # Keep logs from the last 24 hours, delete older ones
    _cleanup_count = 0
    _now = time.time()
    _keep_age = 86400  # 24 hours in seconds
    for _dir in (THINKING_LOG_DIR, SUBAGENT_LOG_DIR, WORK_LOG_DIR):
        if _dir.exists():
            for _f in _dir.iterdir():
                try:
                    if _now - _f.stat().st_mtime > _keep_age:
                        _f.unlink()
                        _cleanup_count += 1
                except Exception:
                    pass

    console.print(f"\n[bold cyan]═══════════════════════════════════════════════[/]")
    console.print(f"[bold cyan]  {APP_NAME} v{VERSION}[/]")
    console.print(f"[bold cyan]  Shell: {SHELL_NAME} | Platform: {platform.system()}[/]")
    config = load_zai_config()
    if config:
        # Show model with provider info
        _cur_model = config.get('model', '?')
        _prov_id, _prov_info = find_provider_for_model(_cur_model)
        if _prov_info:
            _prov_name = _prov_info["name"].get(_get_lang(), _prov_id)
            console.print(f"[success]  ✅ {t('banner.api_ok')} ({_prov_name} {_cur_model})[/success]")
        else:
            console.print(f"[success]  ✅ {t('banner.api_ok')} ({_cur_model})[/success]")
    else:
        console.print(f"[warning]  ⚠️ {t('banner.api_no')}[/warning]")
    # Show permission summary
    _lang = _get_lang()
    perms = get_permissions()
    perm_icons = []
    if _lang == "zh":
        perm_icons.append(f"读取={'✅' if perms.get('allowRead') else '❌'}")
        perm_icons.append(f"写入={'✅' if perms.get('allowWrite') else '❌'}")
        perm_icons.append(f"执行={'✅' if perms.get('allowExecute') else '❌'}")
        perm_icons.append(f"联网={'✅' if perms.get('allowNetwork') else '❌'}")
    else:
        perm_icons.append(f"Read={'✅' if perms.get('allowRead') else '❌'}")
        perm_icons.append(f"Write={'✅' if perms.get('allowWrite') else '❌'}")
        perm_icons.append(f"Exec={'✅' if perms.get('allowExecute') else '❌'}")
        perm_icons.append(f"Net={'✅' if perms.get('allowNetwork') else '❌'}")
    # Dangerous command confirm status
    if _lang == "zh":
        danger_icon = "⚠️需确认" if perms.get('confirmDangerousCommands', True) else "🔓无限制"
    else:
        danger_icon = "⚠️Confirm" if perms.get('confirmDangerousCommands', True) else "🔓Unrestricted"
    console.print(f"[dim]  🔒 {' | '.join(perm_icons)} | {danger_icon}[/dim]")
    console.print(f"[dim]  📂 {get_cwd()}[/dim]")
    console.print(f"[dim]  {t('banner.help')} | {t('banner.perm')} | /cd {t('help.cd_desc')}[/dim]")
    console.print(f"[bold cyan]═══════════════════════════════════════════════[/]\n")

    # Startup picker
    conv = startup_picker()
    if conv is None:
        conv = Conversation()
        # Ask for name
        try:
            name = input(t("picker.input_name")).strip()
            if name:
                conv.title = name
        except (EOFError, KeyboardInterrupt):
            pass

    # Run agent loop
    try:
        agent_loop(conv)
    except KeyboardInterrupt:
        conv.save()
        console.print(f"\n[success]{t('cmd.exit_save')}[/success]")

if __name__ == "__main__":
    main()
