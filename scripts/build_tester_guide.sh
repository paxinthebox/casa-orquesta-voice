#!/usr/bin/env bash
# Build docs/TESTER_GUIDE_es-MX.pdf from docs/tester_guide.md via pandoc.
#
# Usage:
#   ./scripts/build_tester_guide.sh                     # → docs/TESTER_GUIDE_es-MX.pdf
#   ./scripts/build_tester_guide.sh --skip-pandoc       # validate front-matter only
#
# Requires: pandoc + a LaTeX engine (xelatex preferred).
# On macOS:  brew install pandoc basictex
# On Linux:  apt install pandoc texlive-xetex texlive-fonts-recommended
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/docs/tester_guide.md"
OUT="$ROOT/docs/TESTER_GUIDE_es-MX.pdf"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
step() { echo -e "\n${YELLOW}━━━ $1 ━━━${NC}"; }
ok()   { echo -e "${GREEN}✓ $1${NC}"; }
fail() { echo -e "${RED}✗ $1${NC}"; exit 1; }

# -------------------------------------------------------------------
# Front-matter / structure sanity check (always runs; hermetic).
# -------------------------------------------------------------------
step "Front-matter + structure"

[ -f "$SRC" ] || fail "missing $SRC"

# Each top-level section we care about.
for section in \
    "Bienvenido a la beta" \
    "Instalación" \
    "Permisos del micrófono" \
    "¿Cómo funciona?" \
    "Qué probar" \
    "Cómo reportar" \
    "Privacidad" \
    "Cierre"; do
    grep -qF "$section" "$SRC" \
        || fail "tester_guide.md missing section: $section"
done
ok "all 8 numbered sections present"

# Light front-matter check.
head -n 10 "$SRC" | grep -q '^title:' || fail "missing 'title:' in front-matter"
head -n 10 "$SRC" | grep -q '^lang: es-MX' || fail "missing 'lang: es-MX'"
ok "front-matter has title + lang: es-MX"

# Contact-table anchor lives at the bottom.
tail -n 20 "$SRC" | grep -q "Contacto rápido" \
    || fail "missing 'Contacto rápido' anchor at end"
ok "contact table present"

# Optional skip flag for the verify gate (we can't install pandoc in CI sandbox).
if [ "${1:-}" = "--skip-pandoc" ] || [ "${SKIP_PANDOC:-0}" = "1" ]; then
    echo -e "${YELLOW}  (--skip-pandoc): not building the PDF${NC}"
    exit 0
fi

# -------------------------------------------------------------------
# Pandoc build (operator-run; needs LaTeX installed).
# -------------------------------------------------------------------
step "Pandoc → PDF"
command -v pandoc >/dev/null 2>&1 \
    || fail "pandoc not installed. See scripts/build_tester_guide.sh header."

pandoc "$SRC" \
    --from markdown+yaml_metadata_block \
    --pdf-engine=xelatex \
    --toc --toc-depth=2 \
    --variable mainfont:"Helvetica" \
    --variable monofont:"Menlo" \
    --variable papersize:a5 \
    --variable geometry:"margin=15mm" \
    --variable colorlinks:true \
    --output "$OUT"

ok "wrote $OUT ($(wc -c < "$OUT" | tr -d ' ') bytes)"
