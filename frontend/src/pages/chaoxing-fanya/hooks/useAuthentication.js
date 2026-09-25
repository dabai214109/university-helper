import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useRuntimeProfile } from '../../../components'
import { isAuthenticated, removeToken } from '../../../utils/auth'
import { api } from '../../../utils/api'
import { readLastUsername, saveLastUsername } from '../../../utils/chaoxingCreds'
import useChaoxingQrLogin from '../../chaoxing-shared/useChaoxingQrLogin'
import { TOKEN_ERROR } from '../utils'


export default function useAuthentication({ stopPolling }) {


  const navigate = useNavigate()
  const { isLocal } = useRuntimeProfile()


  // Recall the account shared with the signin page so it isn't retyped here.
  const [username, setUsername] = useState(readLastUsername)


  const [password, setPassword] = useState('')


  const [loginLoading, setLoginLoading] = useState(false)


  const [courses, setCourses] = useState([])


  const [error, setError] = useState('')


  const [notice, setNotice] = useState('')


  // True once a Chaoxing session exists — whether it came from a scanned QR
  // code or a password. Both make the password optional for starting a task.
  const [loggedIn, setLoggedIn] = useState(false)


  const [nickname, setNickname] = useState('')


  const mountedRef = useRef(true)
  const courseRequestIdRef = useRef(0)
  const [coursesLoading, setCoursesLoading] = useState(false)


  // Persist the account so the signin page recalls it too.
  useEffect(() => {
    saveLastUsername(username)
  }, [username])


  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      courseRequestIdRef.current += 1
    }
  }, [])


  useEffect(() => {


    if (!isLocal && !isAuthenticated()) {


      navigate('/login', { replace: true })


    }


  }, [isLocal, navigate])


  const onAuthError = useCallback(


    (message) => {


      if (!isLocal && TOKEN_ERROR.test(String(message || ''))) {


        stopPolling()


        removeToken()


        navigate('/login', { replace: true })


        return true


      }


      return false


    },


    [isLocal, navigate, stopPolling]


  )


  const callApi = useCallback(


    async (endpoint, options = {}) => {


      try {


        return await api(endpoint, options)


      } catch (err) {


        const message = err?.message || '请求失败'


        if (onAuthError(message)) return null


        throw err


      }


    },


    [onAuthError]


  )


  const loadCourses = useCallback(async () => {
    const requestId = courseRequestIdRef.current + 1
    courseRequestIdRef.current = requestId
    const isCurrentRequest = () => (
      mountedRef.current && courseRequestIdRef.current === requestId
    )

    if (!isCurrentRequest()) return

    setError('')
    setNotice('')
    setCoursesLoading(true)

    try {
      const resp = await callApi('/chaoxing/courses')

      if (!resp || !isCurrentRequest()) return

      const list = Array.isArray(resp?.courses) ? resp.courses : Array.isArray(resp?.data) ? resp.data : []

      if (!isCurrentRequest()) return
      setCourses(list)
      setNotice(list.length > 0 ? `已获取 ${list.length} 门课程。` : '未查询到课程。')
    } catch (err) {
      if (isCurrentRequest()) setError(err?.message || '获取课程失败。')
    } finally {
      if (isCurrentRequest()) setCoursesLoading(false)
    }
  }, [callApi])


  const refreshLoginStatus = useCallback(async () => {
    try {
      const resp = await callApi('/chaoxing/login-status')
      if (!mountedRef.current) return false
      const data = resp?.data || {}
      const nextLoggedIn = Boolean(data.logged_in)
      setLoggedIn(nextLoggedIn)
      setNickname(String(data.nickname || ''))
      return nextLoggedIn
    } catch {
      // Status is advisory; a failure must not surface as a page error.
      return false
    }
  }, [callApi])


  // Called once a QR scan is confirmed: the backend now holds the session, so
  // the page only needs to pull the course list.
  const handleQrSuccess = useCallback(async () => {
    setLoggedIn(true)
    setError('')
    setNotice('')
    await refreshLoginStatus()
    await loadCourses()
  }, [loadCourses, refreshLoginStatus])


  // `callApi(endpoint, options)` already matches the shared hook's
  // `request(path, options)` contract, so it is passed through directly.
  const qrLogin = useChaoxingQrLogin({ request: callApi, onSuccess: handleQrSuccess })


  const handleLogin = useCallback(


    async (event) => {


      event.preventDefault()


      if (!mountedRef.current) return


      const loginUsername = username.trim()
      const loginPassword = password


      setError('')


      setNotice('')


      if (!loginUsername || !loginPassword.trim()) {


        setError('请输入超星账号和密码。')


        return


      }


      setLoginLoading(true)


      try {


        const loginResp = await callApi('/chaoxing/login', {


          method: 'POST',


          body: JSON.stringify({ username: loginUsername, password: loginPassword })


        })


        if (!loginResp || !mountedRef.current) return


        setUsername(loginUsername)
        setPassword(loginPassword)
        setLoggedIn(true)
        await loadCourses()


      } catch (err) {


        if (mountedRef.current) setError(err?.message || '登录失败。')


      } finally {


        if (mountedRef.current) setLoginLoading(false)


      }


    },


    [callApi, loadCourses, password, username]


  )


  return {
    username, setUsername,
    password, setPassword,
    loginLoading,
    coursesLoading,
    courses, setCourses,
    error, setError,
    notice, setNotice,
    callApi,
    handleLogin,
    loadCourses,
    loggedIn,
    nickname,
    refreshLoginStatus,
    qrLogin,
  }


}
