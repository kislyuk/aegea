export AWS_DEFAULT_AZ=$(aegea-imds placement/availability-zone)
export AWS_DEFAULT_REGION=$(aegea-imds placement/region)
aws configure set default.region $AWS_DEFAULT_REGION
