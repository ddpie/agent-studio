import * as cdk from "aws-cdk-lib";
import * as cognito from "aws-cdk-lib/aws-cognito";
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
      return;
    }

    const userPool = new cognito.UserPool(this, "UserPool", {
      userPoolName: "agent-studio-users",
      selfSignUpEnabled: true,
      signInAliases: { email: true },
      autoVerify: { email: true },
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
