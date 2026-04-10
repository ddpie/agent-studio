declare const __AGENT_STUDIO_REGION__: string;
declare const __AGENT_STUDIO_ACCOUNT_ID__: string;
declare const __AGENT_STUDIO_META_AGENT_ID__: string;
declare const __AGENT_STUDIO_COGNITO_USER_POOL_ID__: string;
declare const __AGENT_STUDIO_COGNITO_CLIENT_ID__: string;
declare const __AGENT_STUDIO_S3_BUCKET__: string;
declare const __AGENT_STUDIO_API_URL__: string;

const region = __AGENT_STUDIO_REGION__;
const accountId = __AGENT_STUDIO_ACCOUNT_ID__;
const metaAgentId = __AGENT_STUDIO_META_AGENT_ID__;

export const awsConfig = {
  Auth: {
    Cognito: {
      userPoolId: __AGENT_STUDIO_COGNITO_USER_POOL_ID__,
      userPoolClientId: __AGENT_STUDIO_COGNITO_CLIENT_ID__,
    },
  },
};

export const agentConfig = {
  region,
  accountId,
  s3Bucket: __AGENT_STUDIO_S3_BUCKET__,
  apiUrl: __AGENT_STUDIO_API_URL__,
  metaAgentId,
  metaAgentArn: `arn:aws:bedrock-agentcore:${region}:${accountId}:runtime/${metaAgentId}`,
};
