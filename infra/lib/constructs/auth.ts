import * as cdk from "aws-cdk-lib";
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as cr from "aws-cdk-lib/custom-resources";
import { Construct } from "constructs";

export interface AuthProps {
  existingUserPoolId?: string;
  existingClientId?: string;
}

export class Auth extends Construct {
  public readonly userPoolId: string;
  public readonly userPoolClientId: string;
  public readonly userPoolArn: string;

  constructor(scope: Construct, id: string, props?: AuthProps) {
    super(scope, id);

    if (props?.existingUserPoolId && props?.existingClientId) {
      this.userPoolId = props.existingUserPoolId;
      this.userPoolClientId = props.existingClientId;
      const imported = cognito.UserPool.fromUserPoolId(this, "ImportedPool", props.existingUserPoolId);
      this.userPoolArn = imported.userPoolArn;

      // Enable TOTP MFA on the existing pool (OPTIONAL = users choose to enable).
      new cr.AwsCustomResource(this, "EnableMfa", {
        onCreate: {
          service: "CognitoIdentityServiceProvider",
          action: "setUserPoolMfaConfig",
          parameters: {
            UserPoolId: props.existingUserPoolId,
            MfaConfiguration: "ON",
            SoftwareTokenMfaConfiguration: { Enabled: true },
          },
          physicalResourceId: cr.PhysicalResourceId.of(`${props.existingUserPoolId}-mfa`),
        },
        onUpdate: {
          service: "CognitoIdentityServiceProvider",
          action: "setUserPoolMfaConfig",
          parameters: {
            UserPoolId: props.existingUserPoolId,
            MfaConfiguration: "ON",
            SoftwareTokenMfaConfiguration: { Enabled: true },
          },
          physicalResourceId: cr.PhysicalResourceId.of(`${props.existingUserPoolId}-mfa`),
        },
        policy: cr.AwsCustomResourcePolicy.fromSdkCalls({
          resources: [imported.userPoolArn],
        }),
      });

      // Create platform-admins group (idempotent — ignores GroupExistsException).
      new cr.AwsCustomResource(this, "PlatformAdminGroup", {
        onCreate: {
          service: "CognitoIdentityServiceProvider",
          action: "createGroup",
          parameters: {
            UserPoolId: props.existingUserPoolId,
            GroupName: "platform-admins",
            Description: "Platform administrators — can approve IAM role creation and service grants",
          },
          physicalResourceId: cr.PhysicalResourceId.of(`${props.existingUserPoolId}-platform-admins`),
          ignoreErrorCodesMatching: "GroupExistsException",
        },
        policy: cr.AwsCustomResourcePolicy.fromSdkCalls({
          resources: [imported.userPoolArn],
        }),
      });

      return;
    }

    const userPool = new cognito.UserPool(this, "UserPool", {
      userPoolName: "agent-studio-users",
      selfSignUpEnabled: true,
      signInAliases: { email: true },
      autoVerify: { email: true },
      mfa: cognito.Mfa.REQUIRED,
      mfaSecondFactor: { otp: true, sms: false },
      standardAttributes: {
        email: { required: true, mutable: false },
      },
      passwordPolicy: {
        minLength: 8,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: false,
      },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // Platform admin group for CDK-created pools.
    new cognito.CfnUserPoolGroup(this, "PlatformAdminGroup", {
      userPoolId: userPool.userPoolId,
      groupName: "platform-admins",
      description: "Platform administrators — can approve IAM role creation and service grants",
    });

    const client = new cognito.UserPoolClient(this, "UserPoolClient", {
      userPool,
      userPoolClientName: "agent-studio-web",
      generateSecret: false,
      authFlows: { userPassword: true, userSrp: true },
      preventUserExistenceErrors: true,
    });

    this.userPoolId = userPool.userPoolId;
    this.userPoolClientId = client.userPoolClientId;
    this.userPoolArn = userPool.userPoolArn;

    new cdk.CfnOutput(this, "UserPoolId", { value: userPool.userPoolId });
    new cdk.CfnOutput(this, "UserPoolClientId", { value: client.userPoolClientId });
  }
}
