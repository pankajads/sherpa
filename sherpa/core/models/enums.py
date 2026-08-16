from enum import StrEnum


class ResourceType(StrEnum):
    # Compute
    EC2_INSTANCE = "aws::ec2::instance"
    EC2_SECURITY_GROUP = "aws::ec2::security-group"
    EC2_VPC = "aws::ec2::vpc"
    EC2_SUBNET = "aws::ec2::subnet"
    ECS_CLUSTER = "aws::ecs::cluster"
    ECS_SERVICE = "aws::ecs::service"
    ECS_TASK_DEFINITION = "aws::ecs::task-definition"
    EKS_CLUSTER = "aws::eks::cluster"
    LAMBDA_FUNCTION = "aws::lambda::function"
    # Load balancing
    ELB = "aws::elasticloadbalancingv2::loadbalancer"
    ELB_TARGET_GROUP = "aws::elasticloadbalancingv2::targetgroup"
    # Storage
    S3_BUCKET = "aws::s3::bucket"
    RDS_INSTANCE = "aws::rds::db-instance"
    RDS_CLUSTER = "aws::rds::db-cluster"
    DYNAMODB_TABLE = "aws::dynamodb::table"
    ELASTICACHE_CLUSTER = "aws::elasticache::cluster"
    # Messaging
    SQS_QUEUE = "aws::sqs::queue"
    SNS_TOPIC = "aws::sns::topic"
    EVENTBRIDGE_RULE = "aws::events::rule"
    # DNS
    ROUTE53_ZONE = "aws::route53::hostedzone"
    # IAM
    IAM_ROLE = "aws::iam::role"


class DependencyType(StrEnum):
    USES = "uses"
    READS_FROM = "reads_from"
    WRITES_TO = "writes_to"
    INVOKES = "invokes"
    NETWORK = "network"
    DEPLOYS_TO = "deploys_to"
    DECLARES = "declares"


class DependencyPlane(StrEnum):
    CLOUD = "cloud"
    CODE = "code"
    PIPELINE = "pipeline"
    CROSS = "cross"


class IaCType(StrEnum):
    TERRAFORM = "terraform"
    CLOUDFORMATION = "cloudformation"
    CDK = "cdk"
    NONE = "none"


class MigrationPath(StrEnum):
    LIFT_AND_SHIFT = "lift-and-shift"
    REPLATFORM = "replatform"
    REARCHITECT = "rearchitect"
    UNKNOWN = "unknown"
