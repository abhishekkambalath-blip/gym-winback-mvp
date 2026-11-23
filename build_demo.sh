#!/bin/sh
# Builds the static Vercel demo in demo/: the same dashboard (ui/) plus an in-browser mock backend.
# Deploy with:  cd demo && npx vercel --prod
cd "$(dirname "$0")" || exit 1
rm -rf demo && mkdir -p demo/static
cp ui/style.css ui/app.js demo_src/mock-backend.js demo/static/
sed 's#<script src="/static/app.js"></script>#<script src="/static/mock-backend.js"></script>\n  <script src="/static/app.js"></script>#' ui/index.html > demo/index.html
printf '{\n  "cleanUrls": true\n}\n' > demo/vercel.json
echo "Built demo/ ($(ls demo/static | wc -l | tr -d ' ') assets)"
