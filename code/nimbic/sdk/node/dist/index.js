"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __exportStar = (this && this.__exportStar) || function(m, exports) {
    for (var p in m) if (p !== "default" && !Object.prototype.hasOwnProperty.call(exports, p)) __createBinding(exports, m, p);
};
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.SaaN ShieldClient = void 0;
exports.patchOpenAI = patchOpenAI;
const openai_1 = __importDefault(require("openai"));
// Re-export all types and classes from the official openai package
__exportStar(require("openai"), exports);
class SaaN ShieldClient extends openai_1.default {
    constructor(config = {}) {
        const apiKey = config.apiKey || process.env.SaaN Shield_API_KEY || '';
        const baseURL = config.baseURL || process.env.SaaN Shield_BASE_URL || 'https://api.SaaN Shield.ai/v1';
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
            apiKey: apiKey || 'SaaN Shield-dummy-key',
            baseURL,
            defaultHeaders,
        });
        // Intercept chat.completions.create to support a providerKey or provider_key parameter
        const completions = this.chat.completions;
        const origCreate = completions.create.bind(completions);
        completions.create = (params, options = {}) => {
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
exports.SaaN ShieldClient = SaaN ShieldClient;
/**
 * Patches an existing OpenAI Node.js client instance to route through
 * the SaaN Shield gateway instead, enabling a zero-code-change migration.
 */
function patchOpenAI(openaiInstance, baseURL = 'https://api.SaaN Shield.ai/v1') {
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
