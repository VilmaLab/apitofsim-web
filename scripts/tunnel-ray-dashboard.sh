#!/usr/bin/env bash

if [[ $# -ne 1 || -z $1 || $1 == -* ]]; then
    printf 'Usage: %s POD_NAME\n' "${0##*/}" >&2
    exit 2
fi

exec oc port-forward --address=127.0.0.1 "pod/$1" 8265:8265
