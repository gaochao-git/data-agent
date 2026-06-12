import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"
import copy from 'copy-to-clipboard';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * 使用copy-to-clipboard库复制文本，提供更好的浏览器兼容性
 * @param text 要复制的文本
 * @returns Promise<boolean> 复制是否成功
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    const success = copy(text);
    return success;
  } catch (error) {
    console.error('Failed to copy text to clipboard:', error);
    return false;
  }
}
