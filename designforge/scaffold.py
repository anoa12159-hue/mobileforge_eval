"""
Scaffold — deterministic project infrastructure managed by code, not LLM.

Design principle: "Deterministic Envelope, LLM Interior"

  Scaffold files (package.json, vite/postcss/tailwind configs, main.tsx,
  index.css, index.html) form a deterministic *envelope* around the project.
  LLM agents generate the *interior* (pages, router, App, context, services).

  The envelope is ALWAYS written by this module and protected from LLM writes
  via ToolAgent._protected_paths. This eliminates an entire class of bugs where
  LLM agents accidentally corrupt infrastructure files (missing CSS imports,
  wrong dependency versions, ESM/CJS mismatches, etc.).

Two entry points used by the Pipeline:

  scaffold_project(output_dir)
      Write ALL scaffold files unconditionally.
      Called BEFORE the Merge stage so infrastructure is in place.

  enforce_scaffold(output_dir)
      Re-write only immutable config files (postcss, tailwind, index.css).
      Called AFTER the Build stage as a safety net — Build LLM may legitimately
      modify package.json or tsconfig.json (e.g. adding dependencies), but pure
      config files should never be touched.

  get_protected_paths(output_dir) -> set[str]
      Returns absolute paths of all scaffold-managed files.
      Used by Merge/Build agents to set ToolAgent._protected_paths.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from designforge.tools.file_ops import write_file

logger = logging.getLogger(__name__)


# ======================================================================
# Node.js version requirement  (single source of truth)
# ======================================================================
# Vite 6 requires Node >= 18.  We pin to 20 for LTS stability.
REQUIRED_NODE_MAJOR: int = 20

# ======================================================================
# Template constants
# ======================================================================

PACKAGE_JSON_TEMPLATE = {
    "name": "designforge-output",
    "private": True,
    "version": "0.0.1",
    "type": "module",
    "scripts": {
        "dev": "vite",
        "build": "tsc && vite build",
        "preview": "vite preview",
    },
    "dependencies": {
        "react": "^19.0.0",
        "react-dom": "^19.0.0",
        "react-router-dom": "^7.0.0",
    },
    "devDependencies": {
        "@types/react": "^19.0.0",
        "@types/react-dom": "^19.0.0",
        "@vitejs/plugin-react": "^4.4.0",
        "autoprefixer": "^10.4.20",
        "postcss": "^8.5.0",
        "tailwindcss": "^3.4.0",
        "typescript": "^5.7.0",
        "vite": "^6.0.0",
    },
}

VITE_CONFIG = """\
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
})
"""

TSCONFIG = """\
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "isolatedModules": true,
    "moduleDetection": "force",
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": false,
    "noUnusedParameters": false
  },
  "include": ["src"]
}
"""

INDEX_HTML = """\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>DesignForge Output</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
"""

MAIN_TSX = """\
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
"""

# .cjs extension + module.exports — universally compatible with both
# old Vite (require()) and new Vite (dynamic import of .cjs).
TAILWIND_CONFIG = """\
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: { extend: {} },
  plugins: [],
}
"""

POSTCSS_CONFIG = """\
module.exports = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
"""

INDEX_CSS = """\
@tailwind base;
@tailwind components;
@tailwind utilities;
"""


# ======================================================================
# File registries
# ======================================================================

# Relative paths of ALL scaffold-managed files.
SCAFFOLD_FILES: dict[str, str] = {
    ".nvmrc": str(REQUIRED_NODE_MAJOR),
    "package.json": json.dumps(PACKAGE_JSON_TEMPLATE, indent=2),
    "vite.config.ts": VITE_CONFIG,
    "tsconfig.json": TSCONFIG,
    "index.html": INDEX_HTML,
    "tailwind.config.cjs": TAILWIND_CONFIG,
    "postcss.config.cjs": POSTCSS_CONFIG,
    "src/main.tsx": MAIN_TSX,
    "src/index.css": INDEX_CSS,
}

# Subset that should NEVER be modified by any LLM agent.
# Build LLM may legitimately modify package.json (add deps) and
# tsconfig.json, so those are excluded from the immutable set.
_IMMUTABLE_FILES: set[str] = {
    ".nvmrc",
    "tailwind.config.cjs",
    "postcss.config.cjs",
    "src/index.css",
    "src/main.tsx",
    "index.html",
    "vite.config.ts",
}


# ======================================================================
# Public API
# ======================================================================

def scaffold_project(output_dir: str) -> None:
    """Write ALL scaffold files unconditionally. Idempotent."""
    base = Path(output_dir)
    for rel_path, content in SCAFFOLD_FILES.items():
        target = base / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        write_file(str(target), content)
        logger.info("scaffolded %s", rel_path)


def enforce_scaffold(output_dir: str) -> None:
    """Re-write only immutable config files. Safety net after Build LLM loop."""
    base = Path(output_dir)
    for rel_path in _IMMUTABLE_FILES:
        content = SCAFFOLD_FILES[rel_path]
        target = base / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        write_file(str(target), content)
    logger.info("enforce_scaffold applied (%d files)", len(_IMMUTABLE_FILES))


def get_protected_paths(output_dir: str) -> set[str]:
    """Return absolute paths of scaffold-managed files for write protection."""
    base = Path(output_dir)
    return {str((base / rel).resolve()) for rel in SCAFFOLD_FILES}
