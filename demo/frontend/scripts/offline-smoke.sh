#!/bin/sh
# Smoke test for a container started with --network none.
# Usage: sh scripts/offline-smoke.sh [container-name]
set -e
C="${1:-tablekeeper}"

echo "1) Outbound network from inside the container (expect: blocked)"
docker exec "$C" node -e "fetch('https://example.com',{signal:AbortSignal.timeout(4000)}).then(()=>{console.log('FAIL: outbound reachable');process.exit(1)},e=>console.log('OK: blocked ('+(e.cause?.code||e.name)+')'))"

echo "2) Home page served (expect: 200)"
docker exec "$C" node -e "fetch('http://127.0.0.1:3000/').then(r=>{console.log('status',r.status);process.exit(r.status===200?0:1)})"

echo "3) Database migrated and seeded (expect: ok true, engine postgres, 4 restaurants)"
docker exec "$C" node -e "fetch('http://127.0.0.1:3000/api/health').then(r=>r.text()).then(console.log)"
