#!/bin/zsh
set -u
cd "$(dirname "$0")" || exit 1
if [[ "$(uname -m)" != arm64 ]]; then
  printf '%s\n' '此包适用于苹果 M 系列 Mac，请使用与电脑芯片匹配的版本。'
  [[ -t 0 ]] && read '?按回车退出'
  exit 1
fi
./runtime/python/bin/python3 -I -B -X utf8 portable.py "$@"
rf_exit=$?
if (( rf_exit != 0 )) && [[ -t 0 ]]; then
  read '?启动失败，请保留上面的信息，按回车退出'
fi
exit $rf_exit
