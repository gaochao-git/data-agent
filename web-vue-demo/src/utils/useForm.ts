/**
 * Vue 版本的表单处理工具
 * 模拟 react-hook-form 的 API
 */
import { ref, computed, watch, type Ref } from 'vue'

export interface FieldError {
  message?: string
  type?: string
}

export interface FormErrors<T> {
  [key: string]: FieldError | undefined
}

export interface UseFormOptions<T> {
  defaultValues?: Partial<T>
  resolver?: (values: T) => { errors: FormErrors<T>; values: T }
  mode?: 'onChange' | 'onBlur' | 'onSubmit'
}

export interface UseFormReturn<T> {
  register: (name: keyof T & string, options?: RegisterOptions) => {
    name: string
    onChange: (e: Event) => void
    onBlur: () => void
    ref: Ref<HTMLInputElement | null>
  }
  handleSubmit: (callback: (data: T) => void | Promise<void>) => (e?: Event) => Promise<void>
  errors: Ref<FormErrors<T>>
  reset: (values?: Partial<T>) => void
  getValues: () => T
  setValue: (name: keyof T & string, value: any) => void
  watch: (name: keyof T & string, callback: (value: any) => void) => () => void
  formState: Ref<{
    isSubmitting: boolean
    isValid: boolean
    isDirty: boolean
  }>
}

export interface RegisterOptions {
  required?: string | boolean
  min?: number | { value: number; message: string }
  max?: number | { value: number; message: string }
  minLength?: number | { value: number; message: string }
  maxLength?: number | { value: number; message: string }
  pattern?: RegExp | { value: RegExp; message: string }
  validate?: (value: any) => string | boolean | Promise<string | boolean>
}

export function useForm<T extends Record<string, any>>(
  options: UseFormOptions<T> = {}
): UseFormReturn<T> {
  const { defaultValues = {}, resolver, mode = 'onSubmit' } = options

  // 表单值
  const values = ref<T>({ ...defaultValues } as T)
  const inputRefs = ref<Record<string, HTMLInputElement | null>>({})

  // 错误状态
  const errors = ref<FormErrors<T>>({})

  // 表单状态
  const formState = ref({
    isSubmitting: false,
    isValid: true,
    isDirty: false,
  })

  // 字段选项（用于验证）
  const fieldOptions = ref<Record<string, RegisterOptions>>({})

  // 验证单个字段
  const validateField = async (name: string, value: any): Promise<string | undefined> => {
    const options = fieldOptions.value[name]
    if (!options) return undefined

    // required
    if (options.required) {
      const isEmpty = value === undefined || value === null || value === ''
      if (isEmpty) {
        return typeof options.required === 'string' ? options.required : `${name} is required`
      }
    }

    // minLength
    if (options.minLength && typeof value === 'string') {
      const minLength = typeof options.minLength === 'number' ? options.minLength : options.minLength.value
      if (value.length < minLength) {
        return typeof options.minLength === 'object' && 'message' in options.minLength
          ? options.minLength.message
          : `${name} must be at least ${minLength} characters`
      }
    }

    // maxLength
    if (options.maxLength && typeof value === 'string') {
      const maxLength = typeof options.maxLength === 'number' ? options.maxLength : options.maxLength.value
      if (value.length > maxLength) {
        return typeof options.maxLength === 'object' && 'message' in options.maxLength
          ? options.maxLength.message
          : `${name} must be at most ${maxLength} characters`
      }
    }

    // pattern
    if (options.pattern) {
      const pattern = options.pattern instanceof RegExp ? options.pattern : options.pattern.value
      if (!pattern.test(value)) {
        return typeof options.pattern === 'object' && 'message' in options.pattern
          ? options.pattern.message
          : `${name} format is invalid`
      }
    }

    // validate function
    if (options.validate) {
      const result = await options.validate(value)
      if (result !== true) {
        return typeof result === 'string' ? result : `${name} is invalid`
      }
    }

    return undefined
  }

  // 验证所有字段
  const validateAll = async (): Promise<boolean> => {
    const newErrors: FormErrors<T> = {}
    let isValid = true

    for (const name of Object.keys(fieldOptions.value)) {
      const error = await validateField(name, values.value[name])
      if (error) {
        newErrors[name] = { message: error, type: 'validation' }
        isValid = false
      }
    }

    // 使用 resolver 进行额外验证
    if (resolver) {
      const result = resolver(values.value)
      if (result.errors && Object.keys(result.errors).length > 0) {
        Object.assign(newErrors, result.errors)
        isValid = false
      }
    }

    errors.value = newErrors
    formState.value.isValid = isValid
    return isValid
  }

  // register - 注册字段
  const register = (name: keyof T & string, options: RegisterOptions = {}) => {
    fieldOptions.value[name] = options

    // 设置默认值
    if (options.required && values.value[name] === undefined) {
      values.value[name] = '' as any
    }

    const elementRef: Ref<HTMLInputElement | null> = ref(null)

    // 监听变化
    watch(elementRef, (el) => {
      if (el) {
        inputRefs.value[name] = el
        // 绑定事件
        el.addEventListener('blur', () => {
          if (mode === 'onBlur' || mode === 'onChange') {
            validateField(name, values.value[name]).then((error) => {
              if (error) {
                errors.value[name] = { message: error, type: 'validation' }
              } else {
                delete errors.value[name]
              }
            })
          }
        })

        el.addEventListener('input', (e) => {
          const target = e.target as HTMLInputElement
          values.value[name] = target.value as any
          formState.value.isDirty = true

          if (mode === 'onChange') {
            validateField(name, target.value).then((error) => {
              if (error) {
                errors.value[name] = { message: error, type: 'validation' }
              } else {
                delete errors.value[name]
              }
            })
          }
        })
      }
    })

    return {
      name,
      ref: elementRef,
      onChange: (e: Event) => {
        const target = e.target as HTMLInputElement
        values.value[name] = target.value as any
        formState.value.isDirty = true
      },
      onBlur: () => {
        if (mode === 'onBlur' || mode === 'onChange') {
          validateField(name, values.value[name]).then((error) => {
            if (error) {
              errors.value[name] = { message: error, type: 'validation' }
            } else {
              delete errors.value[name]
            }
          })
        }
      },
    }
  }

  // handleSubmit - 处理表单提交
  const handleSubmit = (callback: (data: T) => void | Promise<void>) => {
    return async (e?: Event) => {
      if (e) {
        e.preventDefault()
      }

      formState.value.isSubmitting = true

      try {
        const isValid = await validateAll()

        if (isValid) {
          await callback({ ...values.value })
        }
      } finally {
        formState.value.isSubmitting = false
      }
    }
  }

  // reset - 重置表单
  const reset = (newValues?: Partial<T>) => {
    if (newValues) {
      values.value = { ...defaultValues, ...newValues } as T
    } else {
      values.value = { ...defaultValues } as T
    }
    errors.value = {}
    formState.value.isDirty = false
    formState.value.isValid = true
  }

  // getValues - 获取表单值
  const getValues = () => {
    return { ...values.value }
  }

  // setValue - 设置字段值
  const setValue = (name: keyof T & string, value: any) => {
    values.value[name] = value
    formState.value.isDirty = true

    if (mode === 'onChange') {
      validateField(name, value).then((error) => {
        if (error) {
          errors.value[name] = { message: error, type: 'validation' }
        } else {
          delete errors.value[name]
        }
      })
    }
  }

  // watch - 监听字段变化
  const watchField = (name: keyof T & string, callback: (value: any) => void) => {
    return watch(
      () => values.value[name],
      (newVal) => {
        callback(newVal)
      },
      { immediate: true }
    )
  }

  return {
    register,
    handleSubmit,
    errors,
    reset,
    getValues,
    setValue,
    watch: watchField,
    formState,
  }
}
