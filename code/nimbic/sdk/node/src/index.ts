import OpenAI from 'openai';

// Re-export all types and classes from the official openai package
export * from 'openai';

export interface SaaNShieldClientConfig {
  apiKey?: string;
  baseURL?: string;
  providerKey?: string;
  defaultHeaders?: Record<string, string>;
  [key: string]: any;
}

export class SaaNShieldClient extends OpenAI {
  constructor(config: SaaNShieldClientConfig = {}) {
    const apiKey = config.apiKey || process.env.SAAN_SHIELD_API_KEY || '';
    const baseURL = config.baseURL || process.env.SAAN_SHIELD_BASE_URL || 'https://api.saan-shield.ai/v1';
    
    const defaultHeaders = {
      ...(config.defaultHeaders || {}),
    };
    if (apiKey) {
      defaultHeaders['Authorization'] = `Bearer ${apiKey}`;
    }
    if (config.providerKey) {
      defaultHeaders['X-Provider-Key'] = config.providerKey;
    }
    
    super({
      ...config,
      apiKey: apiKey || 'SaaNShield-dummy-key',
      baseURL,
      defaultHeaders,
    });

    // Intercept chat.completions.create to support a providerKey or provider_key parameter
    const completions = this.chat.completions as any;
    const origCreate = completions.create.bind(completions);
    completions.create = (params: any, options: any = {}) => {
      const pKey = params?.providerKey || params?.provider_key;
      if (pKey) {
        const { providerKey, provider_key, ...rest } = params;
        options.headers = {
          ...(options.headers || {}),
          'X-Provider-Key': pKey,
        };
        return origCreate(rest, options);
      }
      return origCreate(params, options);
    };
  }
}

/**
 * Patches an existing OpenAI Node.js client instance to route through
 * the SaaN Shield gateway instead, enabling a zero-code-change migration.
 */
export function patchOpenAI(openaiInstance: any, baseURL: string = 'https://api.saan-shield.ai/v1'): void {
  if (openaiInstance) {
    openaiInstance.baseURL = baseURL;
    if (openaiInstance.apiKey) {
      openaiInstance.defaultHeaders = {
        ...openaiInstance.defaultHeaders,
        'Authorization': `Bearer ${openaiInstance.apiKey}`,
      };
    }
  }
}

