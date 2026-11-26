#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Token Wars - model availability, quota and deployment type check (Prerequisites)
#
# Bash version of check-model-prereqs.ps1: same checks, same output. Needs only bash (3.2+), awk and the Azure CLI.
# Builds the configuration you are about to deploy - the original values in infra/variables.tf plus the overrides you
# uncommented in infra/terraform.tfvars (tfvars wins) - and checks, read-only, per region: model name / format / version
# in the catalog, deployment types offered (GlobalStandard = Global, DataZoneStandard = Data Zone, Standard = Regional),
# retirement dates, max capacity and free quota (limit - used, summed per quota pool).
# Prints every fix both as a terraform.tfvars override and as the original value to change in variables.tf (file:line).
#
# Usage (from infra/):
#   ./scripts/check-model-prereqs.sh                                  # variables.tf + terraform.tfvars overrides
#   ./scripts/check-model-prereqs.sh --location francecentral         # try another primary region
#   ./scripts/check-model-prereqs.sh --scan-regions                   # also search regions where everything fits
#   ./scripts/check-model-prereqs.sh --scan-regions --regions swedencentral,francecentral,eastus2
#   ./scripts/check-model-prereqs.sh --tfvars other.tfvars --subscription <id>   # another overrides file
# Exit code 0 = no blockers. Fireworks feature registration and roles: check-fireworks-prereqs.sh (Challenge 2.4).
# -----------------------------------------------------------------------------
set -u

TFVARS=""
LOCATION=""
SUBSCRIPTION=""
SCAN=0
REGIONS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --tfvars) TFVARS="${2:-}"; shift ;;
    --location) LOCATION="${2:-}"; shift ;;
    --subscription) SUBSCRIPTION="${2:-}"; shift ;;
    --scan-regions) SCAN=1 ;;
    --regions) REGIONS="${2:-}"; shift ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INFRA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
[ -n "$TFVARS" ] || TFVARS="$INFRA_DIR/terraform.tfvars"

if [ -t 1 ]; then
  C_G=$'\033[32m'; C_Y=$'\033[33m'; C_R=$'\033[31m'; C_0=$'\033[0m'; TW_COLOR=1
else
  C_G=""; C_Y=""; C_R=""; C_0=""; TW_COLOR=0
fi
ok()   { printf '  %s[ OK ] %s%s\n' "$C_G" "$*" "$C_0"; }
fail() { printf '  %s[FAIL] %s%s\n' "$C_R" "$*" "$C_0"; }
info() { printf '         %s\n' "$*"; }

echo "Token Wars - model availability, quota and deployment type check"
echo

# 1. Azure CLI + login ------------------------------------------------------------------------
echo "1) Azure CLI"
if ! command -v az >/dev/null 2>&1; then
  fail "Azure CLI (az) not found. Install: https://learn.microsoft.com/cli/azure/install-azure-cli"
  exit 1
fi
if [ -n "$SUBSCRIPTION" ]; then
  az account set --subscription "$SUBSCRIPTION" >/dev/null 2>&1 || { fail "Cannot select subscription $SUBSCRIPTION"; exit 1; }
fi
SUB_ID="$(az account show --query id -o tsv 2>/dev/null | tr -d '\r')"
if [ -z "$SUB_ID" ]; then fail "Not logged in. Run: az login"; exit 1; fi
SUB_NAME="$(az account show --query name -o tsv 2>/dev/null | tr -d '\r')"
if ! TOKEN_ERR="$(az account get-access-token --query expiresOn -o tsv 2>&1 >/dev/null)"; then
  fail "The Azure CLI session expired or is invalid. Run: az login   ($(printf '%s' "$TOKEN_ERR" | tr -d '\r' | tr '\n' ' '))"
  exit 1
fi
ok "Subscription: $SUB_NAME ($SUB_ID)"
info "Quota is per subscription + region + model + deployment type: run this with the subscription you deploy to."

if [ ! -f "$INFRA_DIR/variables.tf" ]; then
  echo "2) Terraform model configuration"
  fail "variables.tf not found in $INFRA_DIR"
  exit 1
fi

TW_TMP="$(mktemp -d 2>/dev/null || mktemp -d -t twmodels)"
trap 'rm -rf "$TW_TMP"' EXIT
TW_TODAY="$(date -u +%Y-%m-%d)"
TW_SOON="$(date -u -d '+45 days' +%Y-%m-%d 2>/dev/null || date -u -v+45d +%Y-%m-%d 2>/dev/null || echo "$TW_TODAY")"

# JMESPath projections: one TSV row per catalog entry (SKUs joined as name|usageName|maxCapacity|retireDate;...).
TW_Q_MODELS='[].[to_string(model.format), to_string(model.name), to_string(model.version), to_string(model.isDefaultVersion), to_string(model.lifecycleStatus), to_string(model.deprecation.inference), join(`";"`, (model.skus || `[]`)[].join(`"|"`, [to_string(name), to_string(usageName), to_string(capacity.maximum), to_string(deprecationDate)]))]'
TW_Q_USAGE='[].[to_string(name.value), to_string(currentValue), to_string(limit)]'

export TW_TMP TW_TODAY TW_SOON TW_COLOR TW_Q_MODELS TW_Q_USAGE
export TW_ERRF="$TW_TMP/az.err"
export TW_VARS="$INFRA_DIR/variables.tf"
export TW_TFVARS="$TFVARS"
export TW_LOCATION="$LOCATION"
export TW_SCAN="$SCAN"
export TW_REGIONS="$REGIONS"

cat > "$TW_TMP/check.awk" <<'AWK'
# ===============================================================================================
# Same logic as check-model-prereqs.ps1, in POSIX awk (gawk, mawk, BSD awk). Arrays are flat (no arrays of arrays).
# ===============================================================================================
function ok(m)   { printf "  %s[ OK ] %s%s\n", CG, m, C0 }
function warn(m) { printf "  %s[WARN] %s%s\n", CY, m, C0; WARNINGS++ }
function fail(m) { printf "  %s[FAIL] %s%s\n", CR, m, C0; BLOCKERS++ }
function info(m) { printf "         %s\n", m }
function say(m, c) { if (c == "") printf "%s\n", m; else printf "%s%s%s\n", c, m, C0 }
function nextstep(m,   i) { for (i = 1; i <= NNEXT; i++) if (NEXTS[i] == m) return; NEXTS[++NNEXT] = m }

function fmt_num(v,   x) {
  if (v == "") return "?"
  x = v + 0
  if (x == int(x)) return sprintf("%.0f", x)
  return x ""
}
function norm_loc(l) { gsub(/[ \t\r\n]/, "", l); return tolower(l) }

# Ordered string lists stored in one array element, items separated by SUBSEP (no duplicates).
function lst_add(L, k, item) {
  if (!(k in L) || L[k] == "") { L[k] = item; return }
  if (index(SUBSEP L[k] SUBSEP, SUBSEP item SUBSEP) == 0) L[k] = L[k] SUBSEP item
}
function lst_has(L, k, item) { return ((k in L) && index(SUBSEP L[k] SUBSEP, SUBSEP item SUBSEP) > 0) }
function lst_split(L, k, A) { if (!(k in L) || L[k] == "") return 0; return split(L[k], A, SUBSEP) }

function sort_strs(A, n,   i, j, t) {
  for (i = 2; i <= n; i++) { t = A[i]; j = i - 1; while (j >= 1 && tolower(A[j]) > tolower(t)) { A[j + 1] = A[j]; j-- }; A[j + 1] = t }
}

function read_file(f,   s, line, first) {
  s = ""; first = 1
  while ((getline line < f) > 0) { sub(/\r$/, "", line); s = s (first ? "" : "\n") line; first = 0 }
  close(f)
  return s
}

# -----------------------------------------------------------------------------------------------
# Minimal HCL reader (same scope as the PowerShell version). Values are stored flat, keyed by path:
#   VT[path] type (str num bool null obj call ref list expr), V[path] value, KEYS[path] ordered child keys,
#   RAW[path] source text of an attribute value, LN[path] line number, NARGS[path] call/list arguments ("#0", "#1"...).
# Paths: "V/..." = variables.tf, "T/..." = terraform.tfvars; blocks use their labels ("V/variable.premium_model/type").
# -----------------------------------------------------------------------------------------------
function addtok(type, val, s, e,   seg) {
  seg = substr(SRC, LPOS, s - LPOS); LLINE += gsub(/\n/, "", seg); LPOS = s
  NT++; TT[NT] = type; TV[NT] = val; TS[NT] = s; TE[NT] = e; TL[NT] = LLINE
}

function tokenize(src,   n, i, c, two, s, sb, depth, cc, rest, marker, k, nl, hl, body, tmp) {
  SRC = src; NT = 0; LPOS = 1; LLINE = 1; n = length(src); i = 1
  while (i <= n) {
    c = substr(src, i, 1)
    if (c == " " || c == "\t" || c == "\n" || c == "\r") { i++; continue }
    two = substr(src, i, 2)
    if (c == "#" || two == "//") { while (i <= n && substr(src, i, 1) != "\n") i++; continue }
    if (two == "/*") { k = index(substr(src, i + 2), "*/"); if (k == 0) i = n + 1; else i = i + k + 3; continue }
    s = i
    if (c == "\"") {
      sb = ""; i++
      while (i <= n) {
        cc = substr(src, i, 1)
        if (cc == "\"") break
        if (cc == "\\" && i < n) { sb = sb substr(src, i + 1, 1); i += 2; continue }
        if (cc == "$" && substr(src, i + 1, 1) == "{") {
          depth = 0
          do { cc = substr(src, i, 1); if (cc == "{") depth++; else if (cc == "}") depth--; sb = sb cc; i++ } while (i <= n && depth > 0)
          continue
        }
        sb = sb cc; i++
      }
      i++
      addtok("str", sb, s, i); continue
    }
    if (two == "<<") {
      rest = substr(src, i, 200)
      if (match(rest, /^<<-?[A-Za-z_][A-Za-z0-9_]*\n/)) {
        marker = substr(rest, 3, RLENGTH - 3); sub(/^-/, "", marker)
        k = i + RLENGTH; body = ""
        while (k <= n) {
          nl = index(substr(src, k), "\n")
          hl = nl ? substr(src, k, nl - 1) : substr(src, k)
          k = nl ? k + nl : n + 1
          tmp = hl; gsub(/^[ \t]+|[ \t\r]+$/, "", tmp)
          if (tmp == marker) break
          body = body hl "\n"
        }
        i = k
        addtok("str", body, s, i); continue
      }
    }
    if (c ~ /[A-Za-z_]/) {
      while (i <= n && substr(src, i, 1) ~ /[A-Za-z0-9_.-]/) i++
      addtok("id", substr(src, s, i - s), s, i); continue
    }
    if (c ~ /[0-9]/) {
      while (i <= n && substr(src, i, 1) ~ /[0-9.eE]/) i++
      addtok("num", substr(src, s, i - s), s, i); continue
    }
    if (two == "==" || two == "!=" || two == ">=" || two == "<=" || two == "&&" || two == "||" || two == "=>") {
      i += 2; addtok("p", two, s, i); continue
    }
    i++
    addtok("p", c, s, i)
  }
}

function tok_is(k, type, val) { return (k >= 1 && k <= NT && TT[k] == type && TV[k] == val) }

function parse_body(path, braced,   key, kl, first, j, lbl) {
  VT[path] = "obj"
  while (P <= NT) {
    if (tok_is(P, "p", "}")) { P++; if (braced) return; continue }
    if (tok_is(P, "p", ",")) { P++; continue }
    if (TT[P] == "id" || TT[P] == "str" || TT[P] == "num") {
      if (tok_is(P + 1, "p", "=") || tok_is(P + 1, "p", ":")) {
        key = TV[P]; kl = TL[P]; P += 2; first = P
        parse_expr(path SEP key)
        lst_add(KEYS, path, key); LN[path SEP key] = kl
        if (first <= NT && P > first) RAW[path SEP key] = substr(SRC, TS[first], TE[P - 1] - TS[first])
        continue
      }
      # Block: identifier, optional labels, then "{" (variable "x" { ... }, validation { ... })
      j = 1; lbl = TV[P]
      while (P + j <= NT && (TT[P + j] == "str" || TT[P + j] == "id")) { lbl = lbl "." TV[P + j]; j++ }
      if (tok_is(P + j, "p", "{")) {
        kl = TL[P]; P += j + 1
        lst_add(KEYS, path, lbl); LN[path SEP lbl] = kl
        parse_body(path SEP lbl, 1)
        continue
      }
    }
    P++
  }
}

function parse_seq(path, closer,   i, before) {
  i = 0
  while (P <= NT) {
    if (tok_is(P, "p", closer)) { P++; break }
    if (tok_is(P, "p", ",")) { P++; continue }
    before = P
    parse_expr(path SEP "#" i)
    i++
    if (P == before) P++
  }
  NARGS[path] = i
}

function parse_primary(path,   t) {
  if (P > NT) { VT[path] = "null"; return }
  t = P; P++
  if (TT[t] == "str") { VT[path] = "str"; V[path] = TV[t]; return }
  if (TT[t] == "num") { VT[path] = "num"; V[path] = TV[t]; return }
  if (TT[t] == "id") {
    if (TV[t] == "true" || TV[t] == "false") { VT[path] = "bool"; V[path] = TV[t]; return }
    if (TV[t] == "null") { VT[path] = "null"; V[path] = ""; return }
    if (tok_is(P, "p", "(")) { P++; VT[path] = "call"; V[path] = TV[t]; parse_seq(path, ")"); return }
    VT[path] = "ref"; V[path] = TV[t]; return
  }
  if (TV[t] == "{") { parse_body(path, 1); return }
  if (TV[t] == "[") { VT[path] = "list"; parse_seq(path, "]"); return }
  if (TV[t] == "(") { parse_expr(path); if (tok_is(P, "p", ")")) P++; return }
  if (TV[t] == "-") { parse_primary(path); if (VT[path] == "num") V[path] = "-" V[path]; else VT[path] = "expr"; return }
  if (TV[t] == "!") { parse_primary(path "#not"); VT[path] = "expr"; return }
  VT[path] = "null"; V[path] = ""
}

function parse_expr(path,   ternary, t) {
  parse_primary(path)
  ternary = 0
  while (P <= NT) {
    if (TT[P] != "p") break
    t = TV[P]
    if (t == "[") { P++; parse_seq(path "#idx", "]"); VT[path] = "expr"; continue }
    if (t == "?") ternary++
    else if (t == ":") { if (ternary <= 0) break; ternary-- }
    else if (t == "}" || t == "," || t == ")" || t == "]" || t == "=" || t == "{" || t == "(") break
    P++
    parse_primary(path "#rhs")
    VT[path] = "expr"
  }
}

function parse_file(prefix, file) {
  tokenize(read_file(file))
  P = 1
  parse_body(prefix, 0)
}

# Effective values: terraform.tfvars replaces the variable; missing optional attributes keep their optional() default.
# Results are returned in RT (type) / RV (value).
function vt(p) { return (p in VT) ? VT[p] : "" }
function vv(p) { return (p in V) ? V[p] : "" }
function tfset(name) { return (("T/" name) in VT) }
function type_obj(var,   p) {
  p = "V/variable." var "/type"
  if (vt(p) == "call" && vv(p) == "map") p = p "/#0"
  if (vt(p) == "call" && vv(p) == "object") return p "/#0"
  return ""
}
function opt_default(var, attr,   o, a) {
  RT = "null"; RV = ""
  o = type_obj(var)
  if (o == "") return
  a = o SEP attr
  if (vt(a) == "call" && vv(a) == "optional" && NARGS[a] >= 2) { RT = vt(a "/#1"); RV = vv(a "/#1") }
}
function obj_attr(var, attr,   t) {
  t = "T/" var
  if (vt(t) == "obj" && ((t SEP attr) in VT)) { RT = VT[t SEP attr]; RV = vv(t SEP attr); return RV }
  opt_default(var, attr)
  return RV
}
function attr_origin(var, attr,   t) { t = "T/" var; return (vt(t) == "obj" && ((t SEP attr) in VT)) ? "tfvars" : "variables.tf" }
function scalar(name) {
  if (tfset(name)) { RT = VT["T/" name]; RV = vv("T/" name) }
  else { RT = vt("V/variable." name "/default"); RV = vv("V/variable." name "/default") }
  return RV
}
function scalar_origin(name) { return tfset(name) ? "tfvars" : "variables.tf" }
function attr_line(var, attr,   o) { o = type_obj(var); return (o != "" && ((o SEP attr) in LN)) ? LN[o SEP attr] : "?" }
function attr_type(var, attr,   o, a) {
  o = type_obj(var); a = o SEP attr
  if (o != "" && vt(a) == "call" && vv(a) == "optional" && vt(a "/#0") == "ref") return vv(a "/#0")
  return "string"
}
function default_line(name,   p) {
  p = "V/variable." name
  if ((p SEP "default") in LN) return LN[p SEP "default"]
  return (p in LN) ? LN[p] : "?"
}
function fw_src() { return tfset("fireworks_models") ? "T/fireworks_models" : "V/variable.fireworks_models/default" }
function fw_attr(key, attr,   p) {
  p = fw_src() SEP key SEP attr
  if (p in VT) { RT = VT[p]; RV = vv(p); return RV }
  opt_default("fireworks_models", attr)
  return RV
}

# -----------------------------------------------------------------------------------------------
# Azure CLI calls. The JMESPath queries come from the environment ("$TW_Q_*"), so the shell never re-parses their quotes.
# -----------------------------------------------------------------------------------------------
function run_az(args,   cmd, line) {
  AZN = 0; AZCODE = 1; AZERR = ""
  cmd = "az " args " 2>\"$TW_ERRF\"; printf '\\n__TW_EXIT__%s\\n' \"$?\""
  while ((cmd | getline line) > 0) {
    sub(/\r$/, "", line)
    if (index(line, "__TW_EXIT__") == 1) { AZCODE = substr(line, 12) + 0; continue }
    if (line != "") AZOUT[++AZN] = line
  }
  close(cmd)
  while ((getline line < ERRF) > 0) { sub(/\r$/, "", line); if (line != "") AZERR = AZERR (AZERR == "" ? "" : " ") line }
  close(ERRF)
}

# Regional catalog + quota, cached per region. The catalog lists each model once per account kind (OpenAI, AIServices):
# entries are merged by format + name + version.
#   CE_*[loc,i]  entry i: format, name, version, default flag, lifecycle, retire date; CS_*[loc,i,j] its SKU j
#   US_*[loc SUBSEP lowercase usage name]  quota usage (current, limit)
function load_catalog(loc,   r, nf, f, key, i, ns, skus, s, np, sp, j, un) {
  if (loc in CAT_DONE) return
  CAT_DONE[loc] = 1
  run_az("cognitiveservices model list --location \"" loc "\" --query \"$TW_Q_MODELS\" -o tsv")
  CAT_OK[loc] = (AZCODE == 0); CAT_ERR[loc] = AZERR; CE_N[loc] = 0
  for (r = 1; r <= AZN; r++) {
    nf = split(AZOUT[r], f, "\t")
    if (nf < 6 || f[2] == "" || f[2] == "null") continue
    key = loc SUBSEP f[1] "|" f[2] "|" f[3]
    if (key in CE_IDX) i = CE_IDX[key]
    else {
      i = ++CE_N[loc]; CE_IDX[key] = i
      CE_F[loc, i] = f[1]; CE_NM[loc, i] = f[2]; CE_V[loc, i] = (f[3] == "null") ? "" : f[3]
      CE_D[loc, i] = 0; CE_L[loc, i] = (f[5] == "null") ? "" : f[5]; CE_R[loc, i] = (f[6] == "null") ? "" : f[6]
      CS_N[loc, i] = 0
    }
    if (f[4] == "true") CE_D[loc, i] = 1
    ns = (nf >= 7) ? split(f[7], skus, ";") : 0
    for (s = 1; s <= ns; s++) {
      np = split(skus[s], sp, "|")
      if (sp[1] == "" || sp[1] == "null") continue
      un = (np >= 2 && sp[2] != "null") ? sp[2] : ""
      if ((loc, i, sp[1]) in CS_IDX) {
        j = CS_IDX[loc, i, sp[1]]
        if (CS_U[loc, i, j] == "" && un != "") CS_U[loc, i, j] = un
        continue
      }
      j = ++CS_N[loc, i]; CS_IDX[loc, i, sp[1]] = j
      CS_NAME[loc, i, j] = sp[1]; CS_U[loc, i, j] = un
      CS_MAX[loc, i, j] = (np >= 3 && sp[3] != "null") ? sp[3] : ""
      CS_R[loc, i, j] = (np >= 4 && sp[4] != "null") ? sp[4] : ""
    }
  }
  run_az("cognitiveservices usage list --location \"" loc "\" --query \"$TW_Q_USAGE\" -o tsv")
  US_OK[loc] = (AZCODE == 0); US_ERR[loc] = AZERR
  for (r = 1; r <= AZN; r++) {
    nf = split(AZOUT[r], f, "\t")
    if (nf < 3 || f[1] == "" || f[1] == "null") continue
    key = loc SUBSEP tolower(f[1])
    US_HAS[key] = 1; US_C[key] = f[2] + 0; US_L[key] = f[3] + 0
  }
}

# Free quota (limit - used) of a pool; FQOK = 0 when the pool is not reported.
function free_quota(loc, usage,   k) {
  FQOK = 0
  if (usage == "") return 0
  k = loc SUBSEP tolower(usage)
  if (!(k in US_HAS)) return 0
  FQOK = 1
  return US_L[k] - US_C[k]
}

function sku_list(loc, e, configured,   j, out, nm, label) {
  out = ""
  for (j = 1; j <= CS_N[loc, e]; j++) {
    nm = CS_NAME[loc, e, j]
    label = (nm in SKULABEL) ? SKULABEL[nm] : "not used"
    out = out (j > 1 ? ", " : "") nm (nm == configured ? "*" : "") " (" label ")"
  }
  return out
}

# -----------------------------------------------------------------------------------------------
# Evaluation of one group of deployments (one Foundry resource) in one region, stored per run id (rid) and deployment.
#   R_ST status, R_LL/R_LM output lines, R_FIXK/R_FIXV tfvars attribute changes that make it work in this region,
#   R_NEED = no in-place fix (model / pay-per-token type / quota not available in the region).
# -----------------------------------------------------------------------------------------------
function add_line(rid, d, lvl, msg,   k) {
  k = ++R_NL[rid, d]; R_LL[rid, d, k] = lvl; R_LM[rid, d, k] = msg
  if (lvl == "fail") R_ST[rid, d] = "fail"
  else if (lvl == "warn" && R_ST[rid, d] == "ok") R_ST[rid, d] = "warn"
}
function set_fix(rid, d, attr, val, why) {
  lst_add(R_FIXK, rid SUBSEP d, attr); R_FIXV[rid, d, attr] = val
  lst_add(R_WHY, rid SUBSEP d, D_LABEL[d] ": " why)
}
function has_fix(rid, d, attr) { return lst_has(R_FIXK, rid SUBSEP d, attr) }
function fix_count(rid, d,   A) { return lst_split(R_FIXK, rid SUBSEP d, A) }
function needs_region(group, rid, d) { return (R_NEED[rid, d] || (group == "secondary" && has_fix(rid, d, "sku"))) }
function in_place(group, rid,   n, DL, k) {
  n = split(G_DEPS[group], DL, " ")
  for (k = 1; k <= n; k++) if (needs_region(group, rid, DL[k])) return 0
  return 1
}

function resolve(rid, d, loc,   i, nb, BN, ci, name, prefix, ns, SIM, SEEN, s, t, x, fmt, f, nfm, FM, k, j, def, e, vers, life, rd, sku, sd, mx) {
  R_ST[rid, d] = "ok"; R_NL[rid, d] = 0; R_NEED[rid, d] = 0; R_E[rid, d] = 0; R_SKUM[rid, d] = 0; R_U[rid, d] = ""
  R_FIXK[rid SUBSEP d] = ""; R_WHY[rid SUBSEP d] = ""
  if (!CAT_OK[loc]) {
    add_line(rid, d, "fail", "cannot read the model catalog of " loc ": " CAT_ERR[loc])
    R_NEED[rid, d] = 1
    return
  }
  name = D_NAME[d]; nb = 0
  for (i = 1; i <= CE_N[loc]; i++) if (CE_NM[loc, i] == name) BN[++nb] = i
  if (nb == 0) {
    ci = ""
    for (i = 1; i <= CE_N[loc]; i++) if (tolower(CE_NM[loc, i]) == tolower(name)) { ci = CE_NM[loc, i]; break }
    if (ci != "") {
      add_line(rid, d, "fail", "'" name "' not found; the catalog name is case-sensitive: '" ci "'")
      set_fix(rid, d, "name", ci, "exact catalog name")
      name = ci
      for (i = 1; i <= CE_N[loc]; i++) if (CE_NM[loc, i] == name) BN[++nb] = i
    } else {
      add_line(rid, d, "fail", "'" name "' is not offered in " loc)
      prefix = name; sub(/[-.].*$/, "", prefix)
      ns = 0; split("", SIM); split("", SEEN)
      for (i = 1; i <= CE_N[loc]; i++) {
        x = CE_NM[loc, i]
        if (index(tolower(x), tolower(prefix)) == 1 && !(x in SEEN)) { SEEN[x] = 1; SIM[++ns] = x }
      }
      sort_strs(SIM, ns)
      t = ""
      for (s = 1; s <= ns && s <= 10; s++) t = t (s > 1 ? ", " : "") SIM[s]
      if (ns > 0) add_line(rid, d, "info", "similar models in " loc ": " t)
      R_NEED[rid, d] = 1
      return
    }
  }
  fmt = D_FMT[d]; nfm = 0
  for (k = 1; k <= nb; k++) if (tolower(CE_F[loc, BN[k]]) == tolower(fmt)) FM[++nfm] = BN[k]
  if (nfm == 0) {
    f = CE_F[loc, BN[1]]
    add_line(rid, d, "fail", "format '" fmt "' does not match the catalog ('" f "')")
    set_fix(rid, d, "format", f, "catalog format")
    for (k = 1; k <= nb; k++) if (CE_F[loc, BN[k]] == f) FM[++nfm] = BN[k]
  }
  for (k = 2; k <= nfm; k++) {
    t = FM[k]; j = k - 1
    while (j >= 1 && ("" CE_V[loc, FM[j]]) > ("" CE_V[loc, t])) { FM[j + 1] = FM[j]; j-- }
    FM[j + 1] = t
  }
  def = 0
  for (k = 1; k <= nfm; k++) if (CE_D[loc, FM[k]]) { def = FM[k]; break }
  if (!def) def = FM[nfm]
  if (D_VER[d] != "") {
    e = 0
    for (k = 1; k <= nfm; k++) if (("" CE_V[loc, FM[k]]) == ("" D_VER[d])) { e = FM[k]; break }
    if (!e) {
      vers = ""
      for (k = 1; k <= nfm; k++) vers = vers (k > 1 ? ", " : "") CE_V[loc, FM[k]]
      add_line(rid, d, "fail", "version " D_VER[d] " is not offered (available: " vers "; default " CE_V[loc, def] ")")
      set_fix(rid, d, "version", CE_V[loc, def], "version offered in the region")
      e = def
    }
  } else {
    e = def
    add_line(rid, d, "info", "version not pinned -> Azure deploys the default version " CE_V[loc, e])
  }
  R_E[rid, d] = e
  life = (CE_L[loc, e] != "") ? ", " CE_L[loc, e] : ""
  add_line(rid, d, "ok", "in catalog: " CE_NM[loc, e] " " CE_V[loc, e] " (" CE_F[loc, e] life ")")
  add_line(rid, d, "info", "deployment types: " sku_list(loc, e, D_SKU[d]))

  rd = substr(CE_R[loc, e], 1, 10)
  if (rd != "" && rd < TODAY) add_line(rid, d, "fail", "model version retired on " rd)
  else if (rd != "" && rd < SOON) add_line(rid, d, "warn", "model version retires on " rd)

  sku = D_SKU[d]
  if (!((loc, e, sku) in CS_IDX)) {
    add_line(rid, d, "fail", "deployment type " sku " is not offered for this model version in " loc)
    R_SKUM[rid, d] = 1
    return
  }
  j = CS_IDX[loc, e, sku]
  R_U[rid, d] = CS_U[loc, e, j]
  sd = substr(CS_R[loc, e, j], 1, 10)
  if (sd != "" && sd < TODAY) add_line(rid, d, "fail", "deployment type " sku " retired on " sd)
  else if (sd != "" && sd < SOON) add_line(rid, d, "warn", "deployment type " sku " retires on " sd)
  mx = CS_MAX[loc, e, j]
  if (mx != "" && mx + 0 > 0 && D_CAP[d] + 0 > mx + 0) {
    add_line(rid, d, "fail", "capacity " fmt_num(D_CAP[d]) " is above the maximum " fmt_num(mx) " per " sku " deployment")
    set_fix(rid, d, "capacity", mx + 0, "maximum capacity per deployment")
  }
}

function quota_phase(rid, d, loc, DEM,   cap, e, na, AS, AF, AK, nall, AL, a, sku, fit, any, unk, allowed, free, uk, total, poolTxt, msg, alt, share, why) {
  cap = has_fix(rid, d, "capacity") ? R_FIXV[rid, d, "capacity"] + 0 : D_CAP[d] + 0
  e = R_E[rid, d]; na = 0
  if (e) {
    nall = split(D_ALLOW[d], AL, ",")
    for (a = 1; a <= nall; a++) {
      sku = AL[a]
      if (sku == D_SKU[d] || !((loc, e, sku) in CS_IDX)) continue
      na++; AS[na] = sku; AF[na] = free_quota(loc, CS_U[loc, e, CS_IDX[loc, e, sku]]); AK[na] = FQOK
    }
  }
  if (R_SKUM[rid, d]) {
    fit = 0; any = 0; unk = 0
    for (a = 1; a <= na; a++) if (AK[a] && AF[a] >= cap) { fit = a; break }
    for (a = 1; a <= na; a++) if (AK[a] && AF[a] >= 1 && (!any || AF[a] > AF[any])) any = a
    for (a = 1; a <= na; a++) if (!AK[a]) { unk = a; break }
    if (fit) {
      add_line(rid, d, "info", "-> " AS[fit] " is offered with " fmt_num(AF[fit]) " free quota")
      set_fix(rid, d, "sku", AS[fit], D_SKU[d] " not offered, " AS[fit] " has quota")
    } else if (any) {
      add_line(rid, d, "info", "-> " AS[any] " is offered but only " fmt_num(AF[any]) " quota is free")
      set_fix(rid, d, "sku", AS[any], D_SKU[d] " not offered")
      set_fix(rid, d, "capacity", int(AF[any]), "free " AS[any] " quota")
    } else if (unk) {
      add_line(rid, d, "warn", "-> " AS[unk] " is offered; its quota is not reported (check Foundry portal > Quota)")
      set_fix(rid, d, "sku", AS[unk], D_SKU[d] " not offered")
    } else {
      allowed = D_ALLOW[d]; gsub(/,/, ", ", allowed)
      add_line(rid, d, "info", "allowed deployment types for this deployment: " allowed " - none has quota here")
      R_NEED[rid, d] = 1
    }
    return
  }
  if (R_U[rid, d] == "") {
    if (e) add_line(rid, d, "warn", "quota pool not reported for " D_SKU[d] "; check Foundry portal > Quota")
    return
  }
  free = free_quota(loc, R_U[rid, d])
  if (!FQOK) {
    why = US_OK[loc] ? "" : " (" US_ERR[loc] ")"
    add_line(rid, d, "warn", "quota pool " R_U[rid, d] " not listed by az cognitiveservices usage list" why "; check Foundry portal > Quota")
    return
  }
  uk = loc SUBSEP tolower(R_U[rid, d])
  total = DEM[tolower(R_U[rid, d])] + 0
  poolTxt = (total != cap) ? " (pool total " fmt_num(total) ")" : ""
  msg = "quota " R_U[rid, d] ": limit " fmt_num(US_L[uk]) ", used " fmt_num(US_C[uk]) " -> free " fmt_num(free) "; needs " fmt_num(cap) poolTxt
  if (free >= total) { add_line(rid, d, "ok", msg); return }
  add_line(rid, d, "fail", msg)
  alt = 0
  for (a = 1; a <= na; a++) if (AK[a] && AF[a] >= cap && (!alt || AF[a] > AF[alt])) alt = a
  share = int(free * cap / total)
  if (alt) {
    add_line(rid, d, "info", "option: sku = \"" AS[alt] "\" (" fmt_num(AF[alt]) " free, keeps capacity " fmt_num(cap) ")")
    set_fix(rid, d, "sku", AS[alt], "not enough " D_SKU[d] " quota, " AS[alt] " has " fmt_num(AF[alt]) " free")
  }
  if (share >= 1) {
    add_line(rid, d, "info", "option: capacity = " fmt_num(share) " (fits the free " D_SKU[d] " quota)")
    if (!alt) set_fix(rid, d, "capacity", share, "free " D_SKU[d] " quota (" fmt_num(free) ")")
  }
  if (!alt && share < 1) R_NEED[rid, d] = 1
  add_line(rid, d, "info", "option: request more quota (Foundry portal > Management center > Quota)")
}

function test_group(rid, group, loc,   n, DL, k, d, cap, DEM) {
  load_catalog(loc)
  split("", DEM)
  n = split(G_DEPS[group], DL, " ")
  for (k = 1; k <= n; k++) resolve(rid, DL[k], loc)
  # Requested capacity per quota pool (deployments of the same model + type in a region share one pool).
  for (k = 1; k <= n; k++) {
    d = DL[k]
    if (R_U[rid, d] == "") continue
    cap = has_fix(rid, d, "capacity") ? R_FIXV[rid, d, "capacity"] + 0 : D_CAP[d] + 0
    DEM[tolower(R_U[rid, d])] += cap
  }
  for (k = 1; k <= n; k++) quota_phase(rid, DL[k], loc, DEM)
}

# -----------------------------------------------------------------------------------------------
# Output helpers: results, region scan, suggestions
# -----------------------------------------------------------------------------------------------
function write_results(rid, group,   n, DL, k, d, ver, i, lvl, m) {
  n = split(G_DEPS[group], DL, " ")
  for (k = 1; k <= n; k++) {
    d = DL[k]
    ver = (D_VER[d] != "") ? D_VER[d] : "(default version)"
    say(sprintf("  - %s: %s %s / %s / %s / capacity %s   [%s]", D_LABEL[d], D_NAME[d], ver, D_FMT[d], D_SKU[d], fmt_num(D_CAP[d]), D_SRC[d]), CW)
    for (i = 1; i <= R_NL[rid, d]; i++) {
      lvl = R_LL[rid, d, i]; m = R_LM[rid, d, i]
      if (lvl == "ok") ok(m); else if (lvl == "warn") warn(m); else if (lvl == "fail") fail(m); else info(m)
    }
    if (D_NOTE[d] != "") info(D_NOTE[d])
  }
}

function fmt_val(attr, v) { return (attr == "capacity" || attr == "secondary_capacity") ? fmt_num(v) : v }
function fix_summary(rid, group,   n, DL, k, d, na, FA, a, out, part) {
  out = ""
  n = split(G_DEPS[group], DL, " ")
  for (k = 1; k <= n; k++) {
    d = DL[k]
    na = lst_split(R_FIXK, rid SUBSEP d, FA)
    if (na == 0) continue
    part = D_LABEL[d]
    for (a = 1; a <= na; a++) part = part (a == 1 ? " " : " ") FA[a] "=" fmt_val(FA[a], R_FIXV[rid, d, FA[a]])
    out = out (out == "" ? "" : "; ") part
  }
  return out
}

function region_scan(group, current,   cands, n, C, U, i, loc, rid, nd, DL, k, d, missing, MS, changed, unknown, bestc, bestu) {
  cands = (REGIONS != "") ? REGIONS : (group == "fireworks" ? FW_SCAN : DEF_SCAN)
  n = split(cands, C, ","); split("", U)
  BEST_RID = 0; BEST_LOC = ""
  for (i = 1; i <= n; i++) {
    loc = norm_loc(C[i])
    if (loc == "" || loc == current || (loc in U)) continue
    U[loc] = 1
    rid = ++NRID
    test_group(rid, group, loc)
    if (!CAT_OK[loc]) { info(sprintf("%-20s catalog not readable", loc)); continue }
    nd = split(G_DEPS[group], DL, " ")
    missing = ""; split("", MS); changed = 0; unknown = 0
    for (k = 1; k <= nd; k++) {
      d = DL[k]
      if (needs_region(group, rid, d) && !(D_NAME[d] in MS)) { MS[D_NAME[d]] = 1; missing = missing (missing == "" ? "" : ", ") D_NAME[d] }
      if (fix_count(rid, d) > 0) changed++
      if (R_ST[rid, d] == "warn") unknown++
    }
    if (missing != "") info(sprintf("%-20s missing: %s", loc, missing))
    else if (changed == 0) say(sprintf("  [ OK ] %-20s fits as configured%s", loc, unknown ? " (" unknown " quota value(s) not reported)" : ""), CG)
    else say(sprintf("  [ OK ] %-20s fits with changes: %s", loc, fix_summary(rid, group)), CG)
    if (missing == "" && (!BEST_RID || changed < bestc || (changed == bestc && unknown < bestu))) {
      BEST_RID = rid; BEST_LOC = loc; bestc = changed; bestu = unknown
    }
  }
}

# Change sets: CS_SK/CS_SV scalars, CS_OB/CS_OA/CS_OV object attributes, CS_FK/CS_FA/CS_FV fireworks_models entries.
function add_fixes(cs, group, rid,   n, DL, k, d, na, FA, a, attr, val, nw, W, w, key) {
  n = split(G_DEPS[group], DL, " ")
  for (k = 1; k <= n; k++) {
    d = DL[k]
    na = lst_split(R_FIXK, rid SUBSEP d, FA)
    if (na == 0 || needs_region(group, rid, d)) continue
    nw = lst_split(R_WHY, rid SUBSEP d, W)
    for (w = 1; w <= nw; w++) lst_add(CS_W, cs, W[w])
    for (a = 1; a <= na; a++) {
      attr = FA[a]; val = R_FIXV[rid, d, attr]
      if (group == "secondary" && attr == "capacity") {
        if (!lst_has(CS_SK, cs, "secondary_capacity") || val + 0 < CS_SV[cs, "secondary_capacity"] + 0) CS_SV[cs, "secondary_capacity"] = val
        lst_add(CS_SK, cs, "secondary_capacity")
      } else if (group == "fireworks") {
        if (attr == "format") { lst_add(CS_SK, cs, "fireworks_model_format"); CS_SV[cs, "fireworks_model_format"] = val; continue }
        key = D_FWKEY[d]
        if (attr == "name") attr = "model"
        lst_add(CS_FK, cs, key); lst_add(CS_FA, cs SUBSEP key, attr); CS_FV[cs, key, attr] = val
      } else {
        lst_add(CS_OB, cs, D_VAR[d]); lst_add(CS_OA, cs SUBSEP D_VAR[d], attr); CS_OV[cs, D_VAR[d], attr] = val
      }
    }
  }
}
function cs_count(cs,   A) { return lst_split(CS_SK, cs, A) + lst_split(CS_OB, cs, A) + lst_split(CS_FK, cs, A) }

function fmt_hcl(attr, v) { return (attr == "capacity" || attr == "secondary_capacity") ? fmt_num(v) : "\"" v "\"" }
function fmt_key(k) { return (k ~ /^[A-Za-z_][A-Za-z0-9_-]*$/) ? k : "\"" k "\"" }

# Re-indents a multi-line raw value (a nested object you already wrote) under the given indentation.
function reindent(raw, indent,   n, L, i, min, t, out) {
  n = split(raw, L, "\n")
  if (n <= 1) return raw
  min = -1
  for (i = 2; i <= n; i++) {
    if (L[i] ~ /^[ \t]*$/) continue
    match(L[i], /^[ \t]*/)
    if (min < 0 || RLENGTH < min) min = RLENGTH
  }
  if (min < 0) min = 0
  out = L[1]
  for (i = 2; i <= n; i++) {
    t = L[i]
    if (length(t) >= min) t = substr(t, min + 1); else sub(/^[ \t]+/, "", t)
    out = out "\n" indent t
  }
  return out
}

# Merges attribute changes (CK[1..nch] -> CV[attr]) into the attributes already written at srcpath (kept verbatim).
function object_assign(name, srcpath, nch, CK, CV, indent,   PK, PV, np, i, k, ns, SK, multi, body, out) {
  np = 0; split("", PV)
  ns = (srcpath != "") ? lst_split(KEYS, srcpath, SK) : 0
  for (i = 1; i <= ns; i++) { k = SK[i]; PK[++np] = k; PV[k] = RAW[srcpath SEP k] }
  for (i = 1; i <= nch; i++) { k = CK[i]; if (!(k in PV)) PK[++np] = k; PV[k] = fmt_hcl(k, CV[k]) }
  multi = 0
  for (i = 1; i <= np; i++) if (index(PV[PK[i]], "\n") > 0) multi = 1
  if (!multi) {
    body = ""
    for (i = 1; i <= np; i++) body = body (i > 1 ? ", " : "") fmt_key(PK[i]) " = " PV[PK[i]]
    return indent fmt_key(name) " = { " body " }"
  }
  out = indent fmt_key(name) " = {"
  for (i = 1; i <= np; i++) out = out "\n" indent "  " fmt_key(PK[i]) " = " reindent(PV[PK[i]], indent "  ")
  return out "\n" indent "}"
}

# A map variable is replaced as a whole: print every entry (current attributes + the changes).
function write_fw_map(name, cs, indent,   src, ns, SK, i, key, nch, CK, CV, a, sp) {
  src = fw_src()
  ns = lst_split(KEYS, src, SK)
  say(indent name " = {", CC)
  for (i = 1; i <= ns; i++) {
    key = SK[i]
    split("", CK); split("", CV)
    nch = lst_split(CS_FA, cs SUBSEP key, CK)
    for (a = 1; a <= nch; a++) CV[CK[a]] = CS_FV[cs, key, CK[a]]
    sp = (vt(src SEP key) == "obj") ? src SEP key : ""
    say(object_assign(key, sp, nch, CK, CV, indent "  "), CC)
  }
  say(indent "}", CC)
}

# Prints the same fix twice: as a terraform.tfvars override and as a change of the original value in variables.tf.
# A variables.tf change has no effect for values that terraform.tfvars overrides: those are flagged instead.
function write_changeset(cs, indent,   nw, W, w, sb, ns, SK, i, k, no, OB, o, var, na, CK, CV, a, attr, nf, FK) {
  if (cs_count(cs) == 0) return
  nw = lst_split(CS_W, cs, W)
  for (w = 1; w <= nw; w++) say(indent "# " W[w], CD)
  sb = indent "  "
  ns = lst_split(CS_SK, cs, SK)
  no = lst_split(CS_OB, cs, OB)
  nf = lst_split(CS_FK, cs, FK)

  say(indent "a) override in terraform.tfvars (takes precedence over variables.tf):", "")
  for (i = 1; i <= ns; i++) { k = SK[i]; say(sb k " = " fmt_hcl(k, CS_SV[cs, k]), CC) }
  for (o = 1; o <= no; o++) {
    var = OB[o]
    split("", CK); split("", CV)
    na = lst_split(CS_OA, cs SUBSEP var, CK)
    for (a = 1; a <= na; a++) CV[CK[a]] = CS_OV[cs, var, CK[a]]
    say(object_assign(var, (vt("T/" var) == "obj") ? "T/" var : "", na, CK, CV, sb), CC)
  }
  if (nf > 0) write_fw_map("fireworks_models", cs, sb)

  say(indent "b) or change the original value in variables.tf:", "")
  for (i = 1; i <= ns; i++) {
    k = SK[i]
    if (scalar_origin(k) == "tfvars") { say(sb "# " k " is overridden in terraform.tfvars: change it there (a variables.tf change has no effect)", CY); continue }
    say(sprintf("%svariables.tf:%-5s variable \"%s\" { default = %s }", sb, default_line(k), k, fmt_hcl(k, CS_SV[cs, k])), CC)
  }
  for (o = 1; o <= no; o++) {
    var = OB[o]
    split("", CK)
    na = lst_split(CS_OA, cs SUBSEP var, CK)
    for (a = 1; a <= na; a++) {
      attr = CK[a]
      if (attr_origin(var, attr) == "tfvars") { say(sb "# " var "." attr " is overridden in terraform.tfvars: change it there (a variables.tf change has no effect)", CY); continue }
      say(sprintf("%svariables.tf:%-5s variable \"%s\": %s = optional(%s, %s)", sb, attr_line(var, attr), var, attr, attr_type(var, attr), fmt_hcl(attr, CS_OV[cs, var, attr])), CC)
    }
  }
  if (nf > 0) {
    if (scalar_origin("fireworks_models") == "tfvars") say(sb "# fireworks_models is overridden in terraform.tfvars: change it there (a variables.tf change has no effect)", CY)
    else {
      say(sprintf("%svariables.tf:%-5s variable \"fireworks_models\": replace its default with", sb, default_line("fireworks_models")), CC)
      write_fw_map("default", cs, sb "  ")
    }
  }
}

# -----------------------------------------------------------------------------------------------
# Configuration: deployments per Foundry resource (group)
# -----------------------------------------------------------------------------------------------
function new_dep(var, label, group, allowed, note) {
  ND++
  D_VAR[ND] = var; D_LABEL[ND] = label; D_GROUP[ND] = group; D_ALLOW[ND] = allowed; D_NOTE[ND] = note; D_FWKEY[ND] = ""
  G_DEPS[group] = G_DEPS[group] " " ND
  return ND
}
function origins(d, var, attrs,   n, A, i, ov) {
  n = split(attrs, A, ","); ov = ""
  for (i = 1; i <= n; i++) if (attr_origin(var, A[i]) == "tfvars") ov = ov (ov == "" ? "" : ", ") A[i]
  D_SRC[d] = D_SRC[d] ((ov != "") ? "; terraform.tfvars overrides: " ov : "; variables.tf")
}
function add_obj_dep(var, label, allowed, note,   d) {
  d = new_dep(var, label, "primary", allowed, note)
  D_NAME[d] = obj_attr(var, "name"); D_FMT[d] = obj_attr(var, "format"); D_VER[d] = obj_attr(var, "version")
  D_SKU[d] = obj_attr(var, "sku"); D_CAP[d] = obj_attr(var, "capacity") + 0
  D_SRC[d] = var
  origins(d, var, "name,version,format,sku,capacity")
}
function add_group(g, label, locvar, loc) { NG++; GORDER[NG] = g; G_LABEL[g] = label; G_LOCVAR[g] = locvar; G_LOC[g] = loc }
function origin_label(o) { return (o == "tfvars") ? "terraform.tfvars" : "variables.tf" }

function main(   tmp, tfexists, n, A, i, k, active, primary_loc, PRI, np, d, c, sec_loc, sec_cap, fw_loc, fw_fmt, src, origin, g, gi, lo, DL, nd, s, step, rid, failing, FG, nfg, names, NMS, has, nblocked, BLK_G, BLK_W, who, letters, letter, cs, dec, decsku, chat, OB, no, o, flags) {
  print "2) Terraform model configuration"
  parse_file("V", VARS)
  ok("Original values: " VARS)
  tfexists = ((getline tmp < TFVARS) >= 0)
  if (tfexists) close(TFVARS)
  if (tfexists) {
    parse_file("T", TFVARS)
    n = lst_split(KEYS, "T", A); active = ""
    for (i = 1; i <= n; i++) {
      k = A[i]
      if ((("V/variable." k) in VT) && k ~ /_model$|^location$|^deploy_|^secondary_|^fireworks_/) active = active (active == "" ? "" : ", ") k
    }
    ok("Overrides: " TFVARS ((active != "") ? " (active: " active ")" : " (no model or region overrides active)"))
  } else {
    info("No overrides: " TFVARS " not found (cp terraform.tfvars.example terraform.tfvars).")
  }

  primary_loc = norm_loc((LOCOPT != "") ? LOCOPT : scalar("location"))
  add_group("primary", "primary Foundry resource", "location", primary_loc)
  add_obj_dep("premium_model", "premium", CHAT, "")
  add_obj_dep("balanced_model", "balanced", CHAT, "")
  add_obj_dep("economy_model", "economy", CHAT, "")
  add_obj_dep("decision_model", "decision", PPT2, "Decision quota units are model-specific (not TPM); pricing_key follows the SKU automatically.")
  add_obj_dep("judge_model", "judge", CHAT, "")
  add_obj_dep("embedding_model", "embedding", CHAT, "")
  if (scalar("deploy_open_model") == "true") add_obj_dep("open_model", "open", CHAT, "")

  if (scalar("deploy_secondary_region") == "true") {
    sec_loc = norm_loc(scalar("secondary_location")); sec_cap = scalar("secondary_capacity") + 0
    add_group("secondary", "secondary Foundry resource (failover)", "secondary_location", sec_loc)
    np = split(G_DEPS["primary"], PRI, " ")
    for (i = 1; i <= 3 && i <= np; i++) {
      c = PRI[i]
      d = new_dep(D_VAR[c], D_LABEL[c] " (secondary)", "secondary", D_ALLOW[c], D_NOTE[c])
      D_NAME[d] = D_NAME[c]; D_FMT[d] = D_FMT[c]; D_VER[d] = D_VER[c]; D_SKU[d] = D_SKU[c]; D_CAP[d] = sec_cap
      D_SRC[d] = D_VAR[c] " + secondary_capacity"
      origins(d, D_VAR[c], "name,version,format,sku")
      if (scalar_origin("secondary_capacity") == "tfvars") D_SRC[d] = D_SRC[d] " (secondary_capacity from terraform.tfvars)"
    }
  }

  if (scalar("deploy_fireworks") == "true") {
    fw_loc = norm_loc(scalar("fireworks_location")); fw_fmt = scalar("fireworks_model_format")
    add_group("fireworks", "Fireworks Foundry resource (Challenge 2.4)", "fireworks_location", fw_loc)
    src = fw_src(); origin = origin_label(scalar_origin("fireworks_models"))
    n = lst_split(KEYS, src, A)
    for (i = 1; i <= n; i++) {
      k = A[i]
      d = new_dep("fireworks_models", k, "fireworks", PPT2, "")
      D_NAME[d] = fw_attr(k, "model"); D_FMT[d] = fw_fmt; D_VER[d] = fw_attr(k, "version")
      D_SKU[d] = fw_attr(k, "sku"); D_CAP[d] = fw_attr(k, "capacity") + 0
      D_FWKEY[d] = k; D_SRC[d] = "fireworks_models." k "; " origin
    }
  }

  for (gi = 1; gi <= NG; gi++) {
    g = GORDER[gi]
    lo = (LOCOPT != "" && G_LOCVAR[g] == "location") ? "--location" : origin_label(scalar_origin(G_LOCVAR[g]))
    info(G_LABEL[g] ": " G_LOCVAR[g] " = " G_LOC[g] " (" lo ")")
    nd = split(G_DEPS[g], DL, " ")
    for (k = 1; k <= nd; k++) {
      d = DL[k]; s = D_SRC[d]; sub(/^[^;]*; /, "", s)
      info(sprintf("  %-22s %s %s / %s / %s / capacity %s   [%s]", D_LABEL[d], D_NAME[d], (D_VER[d] != "") ? D_VER[d] : "(default version)", D_FMT[d], D_SKU[d], fmt_num(D_CAP[d]), s))
    }
  }
  flags = ""
  n = split("deploy_open_model,deploy_secondary_region,deploy_fireworks", A, ",")
  for (i = 1; i <= n; i++) flags = flags (i > 1 ? ", " : "") A[i] " = " ((scalar(A[i]) == "true") ? "true" : "false") " (" origin_label(scalar_origin(A[i])) ")"
  info("Switches: " flags)
  info("Quota 'used' includes every deployment that already exists in the subscription and region")
  info("(your own too if you already ran terraform apply).")

  # 3+. One section per Foundry resource ---------------------------------------------------------
  step = 3
  for (gi = 1; gi <= NG; gi++) {
    g = GORDER[gi]
    print step ") Region " G_LOC[g] " - " G_LABEL[g]
    step++
    load_catalog(G_LOC[g])
    if (!CAT_OK[G_LOC[g]]) {
      fail("cannot read the model catalog of " G_LOC[g] ": " CAT_ERR[G_LOC[g]])
      if (CAT_ERR[G_LOC[g]] ~ /MissingSubscriptionRegistration|not registered/) nextstep("az provider register --namespace Microsoft.CognitiveServices   (then re-run this script)")
      else if (CAT_ERR[G_LOC[g]] ~ /NoRegisteredProviderFound|InvalidLocation|location/) nextstep("Check the region name '" G_LOC[g] "' (" G_LOCVAR[g] "): az account list-locations -o table")
      continue
    }
    if (!US_OK[G_LOC[g]]) warn("cannot read quota usage in " G_LOC[g] ": " US_ERR[G_LOC[g]])
    rid = ++NRID; MAINRID[g] = rid; EVAL[g] = 1
    test_group(rid, g, G_LOC[g])
    write_results(rid, g)
    if (g == "fireworks") {
      nd = split(G_DEPS[g], DL, " ")
      for (k = 1; k <= nd; k++) if (R_NEED[rid, DL[k]] && !R_E[rid, DL[k]]) {
        nextstep("Fireworks models are only listed once Fireworks.EnableDeploy is registered: run scripts/check-fireworks-prereqs.sh --register")
        break
      }
    }
  }

  # Region scan --------------------------------------------------------------------------------------
  nfg = 0
  for (gi = 1; gi <= NG; gi++) {
    g = GORDER[gi]
    if (!(g in EVAL)) continue
    nd = split(G_DEPS[g], DL, " ")
    for (k = 1; k <= nd; k++) if (R_ST[MAINRID[g], DL[k]] == "fail") { FG[++nfg] = g; break }
  }
  if (nfg > 0) {
    if (SCAN) {
      for (i = 1; i <= nfg; i++) {
        g = FG[i]
        nd = split(G_DEPS[g], DL, " "); names = ""; split("", NMS)
        for (k = 1; k <= nd; k++) if (!(D_NAME[DL[k]] in NMS)) { NMS[D_NAME[DL[k]]] = 1; names = names (names == "" ? "" : ", ") D_NAME[DL[k]] }
        print step ") Region scan for the " G_LABEL[g] " (all of: " names ")"
        step++
        region_scan(g, G_LOC[g])
        if (BEST_RID) { MOVE_RID[g] = BEST_RID; MOVE_LOC[g] = BEST_LOC }
        else info("no candidate region fits every model; try --regions with other regions or change the models.")
      }
    } else {
      nextstep("Re-run with --scan-regions to find a region where every model of a failing resource fits.")
    }
  }

  # Summary + suggestions -----------------------------------------------------------------------------
  print ""
  print "Summary: " BLOCKERS " blocker(s), " WARNINGS " warning(s)."
  cs = ++NCS; nblocked = 0
  for (gi = 1; gi <= NG; gi++) {
    g = GORDER[gi]
    if (!(g in EVAL)) continue
    add_fixes(cs, g, MAINRID[g])
    if (!in_place(g, MAINRID[g])) {
      nd = split(G_DEPS[g], DL, " "); who = ""
      for (k = 1; k <= nd; k++) if (needs_region(g, MAINRID[g], DL[k])) who = who (who == "" ? "" : ", ") D_LABEL[DL[k]] " (" D_NAME[DL[k]] ")"
      nblocked++; BLK_G[nblocked] = g; BLK_W[nblocked] = who
    }
  }
  has = (cs_count(cs) > 0)
  nmoves = 0
  for (gi = 1; gi <= NG; gi++) if (GORDER[gi] in MOVE_RID) nmoves++
  if (has || nblocked > 0 || nmoves > 0) {
    print ""
    print "Suggested changes - each fix as a) terraform.tfvars override or b) original value in variables.tf:"
    if (!tfexists) info("(for a) create " TFVARS " first: cp terraform.tfvars.example terraform.tfvars)")
    if (LOCOPT != "" && norm_loc(scalar("location")) != primary_loc) info("(checked with --location " primary_loc ": to use it, set location = \"" primary_loc "\" in terraform.tfvars or variables.tf)")
    letters = "ABCDEFGHIJ"; letter = 1
    if (has || nblocked > 0) {
      print "  Option " substr(letters, letter++, 1) " - keep the current regions:"
      write_changeset(cs, "    ")
      if ("secondary" in G_LOC) {
        no = lst_split(CS_OB, cs, OB)
        for (o = 1; o <= no; o++) if (OB[o] ~ /^(premium|balanced|economy)_model$/ && lst_has(CS_OA, cs SUBSEP OB[o], "sku")) {
          say("    # premium/balanced/economy sku changes also apply to the secondary region: re-run this script after editing.", CD)
          break
        }
      }
      for (i = 1; i <= nblocked; i++) {
        g = BLK_G[i]
        say("    # still blocked in " G_LOC[g] ": " BLK_W[i], CY)
        say("    #   -> pick another model (see 'similar models' above) or change " G_LOCVAR[g], CY)
      }
    }
    for (gi = 1; gi <= NG; gi++) {
      g = GORDER[gi]
      if (!(g in MOVE_RID)) continue
      print "  Option " substr(letters, letter++, 1) " - move the " G_LABEL[g] " to " MOVE_LOC[g] ":"
      c = ++NCS
      lst_add(CS_SK, c, G_LOCVAR[g]); CS_SV[c, G_LOCVAR[g]] = MOVE_LOC[g]
      add_fixes(c, g, MOVE_RID[g])
      write_changeset(c, "    ")
      if (g == "primary") {
        say("    # 'location' moves the whole stack (Foundry, APIM, monitoring): terraform apply recreates it.", CD)
        nd = split(G_DEPS[g], DL, " "); decsku = ""
        for (k = 1; k <= nd; k++) if (D_VAR[DL[k]] == "decision_model") {
          dec = DL[k]
          decsku = has_fix(MOVE_RID[g], dec, "sku") ? R_FIXV[MOVE_RID[g], dec, "sku"] : D_SKU[dec]
        }
        if (decsku == "DataZoneStandard" && index(EU, "," MOVE_LOC[g] ",") == 0)
          say("    # Decision on DataZoneStandard outside the EU: pricing.json ships the EU data-zone price - confirm pricing_key with your coach.", CD)
      }
    }
    chat = lst_has(CS_SK, cs, "secondary_capacity")
    no = lst_split(CS_OB, cs, OB)
    for (o = 1; o <= no; o++) if (OB[o] ~ /^(premium|balanced|economy|judge)_model$/ && lst_has(CS_OA, cs SUBSEP OB[o], "capacity")) chat = 1
    if (chat) info("Lower chat capacity? Also lower \"concurrency\" in strategy.json to avoid 429s during the baseline run.")
    info("Apply each fix either as a) terraform.tfvars override or b) in variables.tf, re-run this script until it")
    info("reports 0 blockers, then: terraform init && terraform apply")
  }
  if (NNEXT > 0) {
    print ""
    print "Next steps:"
    for (i = 1; i <= NNEXT; i++) print "  - " NEXTS[i]
  }
  if (BLOCKERS == 0) {
    print ""
    print "All configured models are available with enough quota. Next (from infra/): terraform init && terraform apply"
  }
}

BEGIN {
  SEP = "/"
  CG = ""; CY = ""; CR = ""; CC = ""; CD = ""; CW = ""; C0 = ""
  if (ENVIRON["TW_COLOR"] == "1") { CG = "\033[32m"; CY = "\033[33m"; CR = "\033[31m"; CC = "\033[36m"; CD = "\033[90m"; CW = "\033[1m"; C0 = "\033[0m" }
  ERRF = ENVIRON["TW_ERRF"]; TODAY = ENVIRON["TW_TODAY"]; SOON = ENVIRON["TW_SOON"]
  VARS = ENVIRON["TW_VARS"]; TFVARS = ENVIRON["TW_TFVARS"]
  LOCOPT = ENVIRON["TW_LOCATION"]; REGIONS = ENVIRON["TW_REGIONS"]; SCAN = (ENVIRON["TW_SCAN"] == "1")
  CHAT = "GlobalStandard,DataZoneStandard,Standard"
  PPT2 = "GlobalStandard,DataZoneStandard"
  SKULABEL["GlobalStandard"] = "Global, pay-per-token"
  SKULABEL["DataZoneStandard"] = "Data Zone, pay-per-token"
  SKULABEL["Standard"] = "Regional, pay-per-token"
  SKULABEL["GlobalProvisionedManaged"] = "Global PTU - not used"
  SKULABEL["DataZoneProvisionedManaged"] = "Data Zone PTU - not used"
  SKULABEL["ProvisionedManaged"] = "Regional PTU - not used"
  SKULABEL["GlobalBatch"] = "Global Batch - not used"
  SKULABEL["DataZoneBatch"] = "Data Zone Batch - not used"
  SKULABEL["DeveloperTier"] = "Developer - not used"
  DEF_SCAN = "swedencentral,francecentral,germanywestcentral,spaincentral,italynorth,polandcentral,norwayeast,switzerlandnorth,uksouth,westeurope,northeurope,eastus,eastus2,westus3,southcentralus,centralus"
  FW_SCAN = "eastus,eastus2,centralus,northcentralus,westus,westus3"
  EU = ",swedencentral,francecentral,francesouth,germanywestcentral,germanynorth,spaincentral,italynorth,polandcentral,norwayeast,norwaywest,switzerlandnorth,switzerlandwest,westeurope,northeurope,swedensouth,belgiumcentral,austriaeast,denmarkeast,"
  BLOCKERS = 0; WARNINGS = 0; NNEXT = 0; NRID = 0; NCS = 0; ND = 0; NG = 0
  main()
  exit (BLOCKERS > 0) ? 1 : 0
}
AWK

awk -f "$TW_TMP/check.awk"
exit $?
