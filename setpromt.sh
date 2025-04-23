# safe_get_ip: get the first non-loopback IPv4 address (works on Linux/macOS/FreeBSD)
safe_get_ip() {
    if command -v ip >/dev/null 2>&1; then
        ip route get 1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1); exit}'
    else
        ifconfig | awk '/inet / && $2 != "127.0.0.1" { print $2; exit }'
    fi
}

# dynamic_ip_prompt: generate the prompt string
dynamic_ip_prompt() {
    local ip
    ip=$(safe_get_ip)
    echo "IP: $ip \\u@\\h:\\w\\$ "
}

# Detect bash
if [ -n "$BASH_VERSION" ]; then
    PROMPT_COMMAND='PS1="$(dynamic_ip_prompt)"'
    export PROMPT_COMMAND

# Detect zsh
elif [ -n "$ZSH_VERSION" ]; then
    precmd() {
        local ip
        ip=$(safe_get_ip)
        PROMPT="IP: $ip %n@%m:%~ %# "
    }
fi

