#!/usr/bin/env bash
# What a real Cube does with a model it pulls from the store, driven the way the portal drives
# it: through the store's /cube proxy. Two refs served at once, a second write picked up, a merge
# with no restart — none of which can be unit tested, because what is under test is Cube.
#
# Two paths are exercised on purpose. The proxy is the real one and covers the whole chain:
# X-Model-Ref -> the store signs a JWT -> contextToAppId picks a compiler cache -> that ref's
# model. The direct-to-Cube path is kept for the fail-soft step alone, because that one asserts
# Cube survives the store going away — which cannot be observed through a proxy inside it.
#
# Self-contained — its own network, database, secret, ports and volume. It never reads
# analyst.db and never touches the portal's own containers.
#
# Beside what it tests rather than in a scripts/ directory: `cube.js` and the compose file one
# level up are exactly what it validates, and `../store/tests` covers the store alone.
#
#   make store-check              run it
#   make store-check ARGS=clean   remove what it left behind
set -uo pipefail
# The repo root, three up: it mounts `services/semantic/cube.js` into the Cube it starts and
# runs its JSON parsing through the root venv's python, so both are relative to there.
cd "$(dirname "$0")/../../.." || exit 1

NET=store-check-net
DB=store-check-db
STORE=store-check-store
CUBE=store-check-cube
CUBESTORE=store-check-cubestore
STORE_PORT=8579
CUBE_PORT=4199
# Production mode makes Cube Store the queue/cache driver and refuses to query without it;
# only dev mode embeds one. `latest` has no arm64 image.
[ "$(uname -m)" = "arm64" ] && CUBESTORE_TAG=${CUBESTORE_TAG:-arm64v8} || CUBESTORE_TAG=${CUBESTORE_TAG:-latest}
READ=read-$RANDOM
WRITE=write-$RANDOM
SECRET=$(head -c 48 /dev/urandom | base64 | tr -d '/+=' | head -c 48)
IMAGE=${STORE_IMAGE:-analyst-model-store:dev}
# Built here if it is missing, so this needs nothing run beforehand. Nothing else in the repo
# builds it: the deployment is the user's to bring up, and `docker compose up` does its own.
build_image() {
  docker image inspect "$IMAGE" >/dev/null 2>&1 && return 0
  printf '  building %s\n' "$IMAGE"
  docker build -q -t "$IMAGE" services/semantic/store >/dev/null
}

W="Authorization: Bearer $WRITE"
R="Authorization: Bearer $READ"
J="Content-Type: application/json"
S=http://127.0.0.1:$STORE_PORT
# The real path: Cube through the store, which mints the token. And Cube's own port, published
# here only so the fail-soft step can reach it with the store stopped.
P=$S/cube/cubejs-api
C=http://127.0.0.1:$CUBE_PORT/cubejs-api

PASS=0
FAIL=0
step() { printf '\n\033[1m%s\033[0m\n' "$1"; }
check() {
  if [ "$2" = "$3" ]; then
    PASS=$((PASS + 1))
    printf '  ok    %s = %s\n' "$1" "$3"
  else
    FAIL=$((FAIL + 1))
    printf '  FAIL  %s\n          want %s\n          got  %s\n' "$1" "$3" "$2"
  fi
}

cleanup() {
  docker rm -f $STORE $CUBE $CUBESTORE $DB >/dev/null 2>&1
  docker volume rm ${STORE}-data >/dev/null 2>&1
  docker network rm $NET >/dev/null 2>&1
}
[ "${1:-}" = "clean" ] && { cleanup; echo cleaned; exit 0; }
cleanup
trap 'echo; echo "left running for inspection — make store-check ARGS=clean"' EXIT

docker network create $NET >/dev/null 2>&1

step "a database of its own"
docker run -d --name $DB --network $NET -e POSTGRES_PASSWORD=check \
  -e POSTGRES_USER=check -e POSTGRES_DB=check postgres:16 >/dev/null
for _ in $(seq 1 40); do
  sleep 1
  docker exec $DB pg_isready -U check -q >/dev/null 2>&1 && break
done
docker exec -i $DB psql -q -U check -d check <<'SQL'
CREATE TABLE album  (album_id  int primary key, title text);
CREATE TABLE artist (artist_id int primary key, name  text);
CREATE TABLE genre  (genre_id  int primary key, name  text);
INSERT INTO album  SELECT g, 'album ' || g  FROM generate_series(1, 347) g;
INSERT INTO artist SELECT g, 'artist ' || g FROM generate_series(1, 275) g;
INSERT INTO genre  SELECT g, 'genre ' || g  FROM generate_series(1, 25)  g;
SQL
echo "  seeded album=347 artist=275 genre=25"

step "the store"
build_image
docker run -d --name $STORE --network $NET -p 127.0.0.1:$STORE_PORT:8500 \
  -e MODEL_STORE_READ_TOKEN="$READ" -e MODEL_STORE_WRITE_TOKEN="$WRITE" \
  -e CUBE_URL=http://$CUBE:4000 -e CUBEJS_API_SECRET="$SECRET" \
  -v ${STORE}-data:/data "$IMAGE" >/dev/null
for _ in $(seq 1 40); do sleep 1; curl -sf $S/health >/dev/null && break; done
check "store health" "$(curl -sS $S/health)" '{"ok":true,"refs":1}'

cube_yml() {
  printf 'cubes:\n  - name: %s\n    sql_table: %s\n    dimensions:\n      - name: id\n        sql: %s_id\n        type: number\n        primary_key: true\n    measures:\n      - name: count\n        type: count\n' "$1" "$1" "$1"
}
put() { curl -sS -X PUT -H "$W" -H "$J" -d @- "$S/files?ref=$1" >/dev/null; }
patch() { curl -sS -X PATCH -H "$W" -H "$J" -d @- "$S/files?ref=$1" >/dev/null; }
body() { .venv/bin/python -c "
import json, sys
print(json.dumps({'message': 'check', sys.argv[1]: {sys.argv[2]: sys.stdin.read()}}))
" "$@"; }

step "main gets one cube, a branch gets two"
cube_yml album | body files cubes/album.yml | put main
curl -sS -X POST -H "$W" -H "$J" -d '{"name":"add-artist","from_ref":"main"}' "$S/refs" >/dev/null
cube_yml artist | body write cubes/artist.yml | patch add-artist

step "Cube — production mode, so the ref arrives on a verified token"
docker run -d --name $CUBESTORE --network $NET \
  -e CUBESTORE_HTTP_PORT=3030 cubejs/cubestore:$CUBESTORE_TAG >/dev/null
docker run -d --name $CUBE --network $NET -p 127.0.0.1:$CUBE_PORT:4000 \
  -e CUBEJS_DB_TYPE=postgres -e CUBEJS_DB_HOST=$DB -e CUBEJS_DB_PORT=5432 \
  -e CUBEJS_DB_NAME=check -e CUBEJS_DB_USER=check -e CUBEJS_DB_PASS=check \
  -e CUBEJS_DEV_MODE=false -e CUBEJS_API_SECRET="$SECRET" \
  -e CUBEJS_CUBESTORE_HOST=$CUBESTORE -e CUBEJS_CUBESTORE_PORT=3030 \
  -e MODEL_STORE_URL=http://$STORE:8500 -e MODEL_STORE_TOKEN="$READ" \
  -v "$PWD/services/semantic/cube.js:/cube/conf/cube.js:ro" \
  cubejs/cube:latest >/dev/null

mint() {
  SECRET="$SECRET" REF="$1" .venv/bin/python -c "
import jwt, os, time
print(jwt.encode({'ref': os.environ['REF'], 'exp': int(time.time()) + 3600},
                 os.environ['SECRET'], algorithm='HS256'))
"
}
MAIN=main
BRANCH=add-artist

names() {
  .venv/bin/python -c "
import json, sys
body = json.load(sys.stdin)
print(','.join(sorted(c['name'] for c in body.get('cubes', []))) or body.get('error', 'none'))
"
}
one() {
  .venv/bin/python -c "
import json, sys
body = json.load(sys.stdin)
rows = body.get('data')
print(rows[0][sys.argv[1] + '.count'] if rows else body.get('error', 'no rows'))
" "$1"
}

# --- through the store, which is how the portal reaches Cube -------------------
# A ref name, not a token: the store signs it. Nothing here holds a Cube credential, which is
# the property being checked as much as the model it returns.
cubes() { curl -sS -H "$W" -H "X-Model-Ref: $1" $P/v1/meta | names; }
count() {
  curl -sS -H "$W" -H "X-Model-Ref: $1" -H "$J" \
    -d "{\"query\":{\"measures\":[\"$2.count\"]}}" $P/v1/load | one "$2"
}

# --- straight at Cube, for the fail-soft step only ----------------------------
direct_cubes() { curl -sS -H "Authorization: $(mint "$1")" $C/v1/meta | names; }
direct_count() {
  curl -sS -H "Authorization: $(mint "$1")" -H "$J" \
    -d "{\"query\":{\"measures\":[\"$2.count\"]}}" $C/v1/load | one "$2"
}
for _ in $(seq 1 60); do sleep 2; [ "$(cubes "$MAIN")" = "album" ] && break; done

step "two refs, one container, the same instant"
check "main" "$(cubes "$MAIN")" "album"
check "branch" "$(cubes "$BRANCH")" "album,artist"

step "a real query through a cube only the branch has"
check "artist.count" "$(count "$BRANCH" artist)" "275"

step "the SECOND write is picked up — an eager factory passes the first and never again"
cube_yml genre | body write cubes/genre.yml | patch add-artist
sleep 8
check "branch" "$(cubes "$BRANCH")" "album,artist,genre"
check "main unaffected" "$(cubes "$MAIN")" "album"

step "a merge reaches main with no restart"
curl -sS -X POST -H "$W" -H "$J" -d '{"from_ref":"add-artist","into":"main"}' "$S/merge" >/dev/null
sleep 8
check "main" "$(cubes "$MAIN")" "album,artist,genre"

# Straight at Cube, necessarily: the proxy lives inside the store, so with the store stopped
# there is nothing to ask through it. What this proves is narrower than it used to be and still
# worth proving — `schemaVersion` runs on *every* request, so a store that stops answering must
# leave Cube serving the model it has rather than taking it down.
#
# What it no longer proves is that the *portal* keeps working. The store is now the query path,
# so a store that is down means no queries at all. That is the cost of one authenticated door.
step "FAIL SOFT — cube.js must degrade to a stale model, not take Cube down"
docker stop $STORE >/dev/null
sleep 8
check "store is down" "$(curl -sf $S/health >/dev/null 2>&1 && echo up || echo down)" "down"
check "meta still served" "$(direct_cubes "$MAIN")" "album,artist,genre"
check "queries still run" "$(direct_count "$MAIN" album)" "347"

step "and recovers"
docker start $STORE >/dev/null
for _ in $(seq 1 40); do sleep 1; curl -sf $S/health >/dev/null && break; done
check "main" "$(cubes "$MAIN")" "album,artist,genre"

trap - EXIT
printf '\n%s passed, %s failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
echo "make store-check ARGS=clean to remove what this left running"
