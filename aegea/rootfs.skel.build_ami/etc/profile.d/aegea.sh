export AWS_DEFAULT_AZ=$(curl -s http://169.254.169.254/latest/dynamic/instance-identity/document | jq -r .availabilityZone)
export AWS_DEFAULT_REGION=$(curl -s http://169.254.169.254/latest/dynamic/instance-identity/document | jq -r .region)
aws configure set default.region $AWS_DEFAULT_REGION
