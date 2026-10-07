version: 0.2

env:
  shell: bash

phases:
  build:
    commands:
      - |
        PARAMS=""
        for var in $(compgen -v); do
          [[ $var == *_PARAM ]] || continue

          IFS=_ read -ra parts <<< "${var%_PARAM}"
          key=""
          for part in "${parts[@]}"; do
            part="${part,,}"
            key+="${part^}"
          done

          PARAMS+="${PARAMS:+,}${key}=${!var}"
        done

        if [[ -z "$PARAMS" ]]; then
          echo "No *_PARAM environment variables found" >&2
          exit 1
        fi

        aws ssm send-command \
          --document-name "$SSM_DOCUMENT_NAME" \
          --targets "Key=InstanceIds,Values=$INSTANCE_ID" \
          --parameters "$PARAMS"
