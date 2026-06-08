import OpenAI from 'openai';
export * from 'openai';
export interface SaaN ShieldClientConfig {
    apiKey?: string;
    baseURL?: string;
    providerKey?: string;
    defaultHeaders?: Record<string, string>;
    [key: string]: any;
}
export declare class SaaN ShieldClient extends OpenAI {
    constructor(config?: SaaN ShieldClientConfig);
}
/**
 * Patches an existing OpenAI Node.js client instance to route through
 * the SaaN Shield gateway instead, enabling a zero-code-change migration.
 */
export declare function patchOpenAI(openaiInstance: any, baseURL?: string): void;
