export interface AgentStudioConfig {
  region: string;
  accountId: string;
  s3Bucket: string;
  cognitoUserPoolId: string;
  cognitoClientId: string;
  cognitoIdentityPoolId: string;
  metaAgentId: string;
}

export function getConfig(): AgentStudioConfig {
  const required = (key: string): string => {
    const val = process.env[key];
    if (!val) throw new Error(`Missing env var: ${key}`);
    return val;
  };
  return {
    region: required("AGENT_STUDIO_REGION"),
    accountId: required("AGENT_STUDIO_ACCOUNT_ID"),
    s3Bucket: required("AGENT_STUDIO_S3_BUCKET"),
    cognitoUserPoolId: required("AGENT_STUDIO_COGNITO_USER_POOL_ID"),
    cognitoClientId: required("AGENT_STUDIO_COGNITO_CLIENT_ID"),
    cognitoIdentityPoolId: required("AGENT_STUDIO_COGNITO_IDENTITY_POOL_ID"),
    metaAgentId: required("AGENT_STUDIO_META_AGENT_ID"),
  };
}
