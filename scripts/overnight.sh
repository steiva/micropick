#!/usr/bin/env bash
# overnight.sh — ночной прогон тикетов из GitHub Issues (созданных /to-tickets из mattpocock/skills).
#
# Что делает:
#   * Берёт открытые issues с меткой ready-for-agent, у которых все блокеры уже готовы
#     (frontier). Блокеры читаются из нативных GitHub-зависимостей и из секции "## Blocked by".
#     Родительские issues (с sub-issues, например PRD #1) не берёт никогда.
#   * Каждый тикет решает отдельным запуском `claude -p` через /implement (свежий контекст = clear).
#   * Все тикеты ночи коммитятся в одну ветку overnight/<дата> от BASE_BRANCH, потому что тикеты
#     зависят друг от друга. Утром: ревью и merge в BASE_BRANCH.
#   * После каждого тикета скрипт сам гоняет тесты: agent-done ставится, только если не появилось
#     новых падений по сравнению с тем, что было до тикета. Иначе needs-human.
#   * Вопросы: Claude решает сам, а вопросы и принятые решения пишутся в QUESTIONS.md и комментарием в issue.
#   * 5-часовой лимит: ждёт и продолжает ту же сессию. Недельный лимит: останавливается.
#   * Mac не засыпает: скрипт перезапускает себя под caffeinate.
#
# Запуск из корня репозитория, внутри tmux:
#   scripts/overnight.sh
#   DRY_RUN=1 scripts/overnight.sh     # только показать, какой тикет взялся бы первым
#
# Утром: QUESTIONS.md и summary.md в $OVERNIGHT_DIR, ветка overnight/<дата>,
#        issues с метками agent-done / needs-human.
#
# bash 3.2 (системный на macOS): без mapfile, ассоциативных массивов и ${x,,}.

set -uo pipefail

# ---------- настройки ----------
READY_LABEL="${READY_LABEL:-ready-for-agent}"
DONE_LABEL="${DONE_LABEL:-agent-done}"          # тикет сделан агентом, ждёт твоего ревью
HUMAN_LABEL="${HUMAN_LABEL:-needs-human}"       # агент застрял, упал или сломал тесты
IMPLEMENT_CMD="${IMPLEMENT_CMD:-/mattpocock-skills:implement}"
BASE_BRANCH="${BASE_BRANCH:-redesign}"
BRANCH="${BRANCH:-overnight/$(date +%F)}"
MAX_TICKETS="${MAX_TICKETS:-12}"                # предохранитель, чтобы не съесть весь недельный лимит
LIMIT_POLL_SEC="${LIMIT_POLL_SEC:-900}"         # при лимите проверять раз в 15 минут
LIMIT_MAX_WAIT_SEC="${LIMIT_MAX_WAIT_SEC:-21600}"  # дольше 6 часов не ждать
OVERNIGHT_DIR="${OVERNIGHT_DIR:-$HOME/overnight}"
TEST_CMD="${TEST_CMD:-conda run -n lab python -m pytest -q -p no:cacheprovider tests}"
DRY_RUN="${DRY_RUN:-}"
# claude: из PATH, иначе самый новый из расширения VS Code.
CLAUDE_BIN="${CLAUDE_BIN:-$(command -v claude || ls -d "$HOME"/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude 2>/dev/null | sort -V | tail -1)}"
# --------------------------------

# Не дать Mac уснуть ночью (и экрану, и системе).
if [[ -z "${OVERNIGHT_CAFFEINATED:-}" && -z "$DRY_RUN" ]] && command -v caffeinate >/dev/null; then
  export OVERNIGHT_CAFFEINATED=1
  exec caffeinate -dims "$0" "$@"
fi

LOGS="$OVERNIGHT_DIR/logs"
QUESTIONS="$OVERNIGHT_DIR/QUESTIONS.md"
SUMMARY="$OVERNIGHT_DIR/summary.md"
mkdir -p "$LOGS"
touch "$QUESTIONS" "$SUMMARY"

ts() { date '+%F %T'; }
say() { echo "[$(ts)] $*"; }

for cmd in gh jq git; do
  command -v "$cmd" >/dev/null || { echo "Нужен $cmd"; exit 1; }
done
[[ -x "$CLAUDE_BIN" ]] || { echo "Не нашёл claude; укажи CLAUDE_BIN=/путь/к/claude"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "gh не залогинен: gh auth login"; exit 1; }

# ---------- зависимости ----------
blockers_of() {  # $1 = номер issue, $2 = тело issue
  {
    gh api "repos/{owner}/{repo}/issues/$1/dependencies/blocked_by" --jq '.[].number' 2>/dev/null
    awk '/^##[[:space:]]*Blocked by/{f=1;next} /^##[[:space:]]/{f=0} f' <<<"$2" \
      | grep -oE '#[0-9]+' | tr -d '#'
  } | sort -un
}

is_done() {  # закрыт или уже сделан агентом этой (или прошлой) ночью
  gh issue view "$1" --json state,labels \
    --jq "(.state==\"CLOSED\") or any(.labels[]; .name==\"$DONE_LABEL\")" 2>/dev/null
}

is_parent() {  # у issue есть sub-issues: это PRD, а не тикет
  [[ "$(gh api "repos/{owner}/{repo}/issues/$1" --jq '.sub_issues_summary.total // 0' 2>/dev/null)" != "0" ]]
}

next_ticket() {  # печатает номер первого тикета на frontier
  local list n body ok b
  list="$(gh issue list --label "$READY_LABEL" --state open --limit 200 --json number,body \
          --jq 'sort_by(.number) | .[] | @base64')"
  for row in $list; do
    n="$(base64 -d <<<"$row" | jq -r .number)"
    body="$(base64 -d <<<"$row" | jq -r .body)"
    is_parent "$n" && continue
    ok=1
    for b in $(blockers_of "$n" "$body"); do
      [[ "$(is_done "$b")" == "true" ]] || { ok=0; break; }
    done
    (( ok )) && { echo "$n"; return 0; }
  done
  return 1
}

if [[ -n "$DRY_RUN" ]]; then
  n="$(next_ticket)" && say "первым взялся бы #$n: $(gh issue view "$n" --json title --jq .title)" \
    || say "готовых тикетов нет"
  exit 0
fi

[[ -z "$(git status --porcelain)" ]] || { echo "Рабочее дерево грязное: закоммить или убери изменения"; exit 1; }

gh label create "$DONE_LABEL"  --color 0E8A16 --description "Сделано агентом, ждёт ревью" --force >/dev/null 2>&1
gh label create "$HUMAN_LABEL" --color D93F0B --description "Агенту нужен человек"        --force >/dev/null 2>&1

# Ветка на всю ночь
if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
  git switch -q "$BRANCH"
else
  git switch -q -c "$BRANCH" "$BASE_BRANCH"
fi
say "работаю в ветке $BRANCH (от $BASE_BRANCH)"
echo -e "\n## Ночь $(date +%F), ветка $BRANCH" >> "$SUMMARY"
echo -e "\n## Ночь $(date +%F)" >> "$QUESTIONS"

# ---------- тесты ----------
failing_tests() {  # id упавших тестов, по одному в строке, отсортированы
  $TEST_CMD 2>&1 | tee "$LOGS/tests-last.log" \
    | grep -E '^(FAILED|ERROR) ' | sed -E 's/^(FAILED|ERROR) //; s/ - .*$//' | sort -u
}

say "базовый прогон тестов"
baseline="$(failing_tests)"
say "до первого тикета падают: $(grep -c . <<<"$baseline" || true)"

SCHEMA='{"type":"object","properties":{
  "status":{"type":"string","enum":["done","partial","blocked"]},
  "summary":{"type":"string"},
  "questions":{"type":"array","items":{"type":"object","properties":{
    "question":{"type":"string"},"decision":{"type":"string"},"if_wrong":{"type":"string"}},
    "required":["question","decision"]}}},
  "required":["status","summary","questions"]}'

# ---------- запуск Claude ----------
make_prompt() {  # $1 = номер issue, $2 = тесты, падавшие до тикета
  cat <<EOF
$IMPLEMENT_CMD GitHub issue #$1

Контекст: это ночной автономный прогон. Меня нет до утра, спросить некого.
- Прочитай тикет: gh issue view $1 --comments. Если в нём есть раздел Parent, прочитай и родительский issue.
- Seams для /tdd выбери сам, исходя из тикета и существующего кода; не спрашивай подтверждения.
- На любой вопрос или неоднозначность прими самое разумное решение и продолжай.
  Каждое такое решение верни в поле questions: вопрос, что ты решил, что придётся переделать, если я отвечу иначе.
- Экономь токены: большие файлы читай кусками (найди место grep'ом, потом Read с offset/limit).
  Code review делай сам, одним проходом (стандарты + тикет), без субагентов. Agent не запускай вообще.
- Тесты: отдельные файлы по ходу работы, весь набор один раз в конце: $TEST_CMD
  (идёт 3-4 минуты; запускай в фоне). Эти тесты падали до тебя, их не чини и не считай своими:
${2:-  (нет)}
- GUI запускай только с --mock. Ничего не запускай на роботе и подключённом железе.
- Работай в текущей ветке. Не переключай ветки, не пушь, не закрывай и не редактируй issues: это сделает скрипт.
- Сообщение коммита должно содержать "Refs #$1".
- Если тикет невозможно сделать без меня: status=blocked. Закоммить то, что успел, только если тесты зелёные.
- В конце status: done, partial или blocked, и summary в 1–3 предложения.
EOF
}

run_claude() {  # $1 = prompt, $2 = session id для продолжения (необязательно)
  local args=(-p "$1" --permission-mode auto --permission-prompts none
              --output-format json --json-schema "$SCHEMA")
  [[ -n "${2:-}" ]] && args+=(--resume "$2")
  "$CLAUDE_BIN" "${args[@]}" 2>>"$LOGS/stderr.log"
}

hit_limit() {  # $1 = вывод claude; печатает weekly / session или ничего
  local text
  text="$(jq -r 'select(.is_error == true) | .result // empty' <<<"$1" 2>/dev/null)"
  [[ -z "$text" ]] && text="$1"
  if grep -qiE "weekly limit" <<<"$text"; then echo weekly
  elif grep -qiE "hit your [^.]*limit|usage limit|limit reached|rate.limit" <<<"$text"; then echo session
  fi
}

# ---------- главный цикл ----------
count=0
while (( count < MAX_TICKETS )); do
  n="$(next_ticket)" || { say "больше нет готовых тикетов"; break; }
  count=$((count + 1))
  title="$(gh issue view "$n" --json title --jq .title)"
  log="$LOGS/issue-$n.json"
  say "▶ #$n $title"

  session=""
  waited=0
  while true; do
    if [[ -z "$session" ]]; then
      out="$(run_claude "$(make_prompt "$n" "$(sed 's/^/  /' <<<"$baseline")")")"
    else
      out="$(run_claude "Лимит сбросился. Продолжай работу над тикетом #$n с того места, где остановился. Правила те же." "$session")"
    fi
    echo "$out" >> "$log"
    s="$(jq -r '.session_id // empty' <<<"$out" 2>/dev/null)"
    [[ -n "$s" ]] && session="$s"

    limit="$(hit_limit "$out")"
    if [[ "$limit" == weekly ]]; then
      say "недельный лимит, останавливаюсь"
      echo "- #$n $title: остановлено на недельном лимите, сессия $session" >> "$SUMMARY"
      exit 0
    fi
    if [[ "$limit" == session ]]; then
      if (( waited >= LIMIT_MAX_WAIT_SEC )); then
        say "лимит не сбросился за $((waited / 3600)) ч, останавливаюсь"
        echo "- #$n $title: остановлено на лимите, сессия $session" >> "$SUMMARY"
        exit 0
      fi
      say "лимит, жду $((LIMIT_POLL_SEC / 60)) мин"
      sleep "$LIMIT_POLL_SEC"
      waited=$((waited + LIMIT_POLL_SEC))
      continue
    fi
    break
  done

  status="$(jq -r '.structured_output.status // "error"' <<<"$out" 2>/dev/null || echo error)"
  summary="$(jq -r '.structured_output.summary // "нет результата, см. лог"' <<<"$out" 2>/dev/null)"
  qs_md="$(jq -r '.structured_output.questions[]? |
    "- **Вопрос:** \(.question)\n  **Решил:** \(.decision)" +
    (if .if_wrong then "\n  **Если иначе:** \(.if_wrong)" else "" end)' <<<"$out" 2>/dev/null)"

  # Незакоммиченные хвосты не должны попасть в следующий тикет
  if [[ -n "$(git status --porcelain)" ]]; then
    git stash push -u -q -m "overnight #$n leftovers"
    say "незакоммиченные изменения #$n убраны в git stash"
  fi

  # Проверка: не сломал ли тикет то, что работало до него
  say "прогон тестов после #$n"
  now_failing="$(failing_tests)"
  broken="$(comm -13 <(echo "$baseline") <(echo "$now_failing") | grep . || true)"
  if [[ -n "$broken" && "$status" == "done" ]]; then
    status="partial"
    summary="$summary Новые падения тестов: $(tr '\n' ' ' <<<"$broken")"
  fi
  baseline="$now_failing"

  # Вопросы: в общий файл и комментарием в issue
  if [[ -n "$qs_md" ]]; then
    printf "\n### #%s %s\n%s\n" "$n" "$title" "$qs_md" >> "$QUESTIONS"
  fi
  commits="$(git log --oneline "$BASE_BRANCH..HEAD" -E --grep "#$n([^0-9]|$)" 2>/dev/null)"
  {
    printf '🤖 Ночной прогон, ветка `%s`\n\n**Статус:** %s\n\n%s\n' "$BRANCH" "$status" "$summary"
    [[ -n "$commits" ]] && printf '\n**Коммиты:**\n```\n%s\n```\n' "$commits"
    [[ -n "$qs_md" ]] && printf '\n**Решения, которые нужно проверить:**\n%s\n' "$qs_md"
  } > "$LOGS/comment-$n.md"
  gh issue comment "$n" --body-file "$LOGS/comment-$n.md" >/dev/null

  # Метки: убираем ready-for-agent, чтобы тикет не взялся повторно
  if [[ "$status" == "done" ]]; then
    gh issue edit "$n" --remove-label "$READY_LABEL" --add-label "$DONE_LABEL" >/dev/null
    say "✓ #$n"
  else
    gh issue edit "$n" --remove-label "$READY_LABEL" --add-label "$HUMAN_LABEL" >/dev/null
    say "✗ #$n ($status)"
  fi
  echo "- #$n $title: $status. $summary" >> "$SUMMARY"
done

say "Готово: $count тикетов. Смотри $SUMMARY и $QUESTIONS; ветка $BRANCH"
