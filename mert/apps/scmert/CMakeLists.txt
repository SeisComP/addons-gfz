SC_ADD_PYTHON_PROG(scmert)
SC_INSTALL_INIT(scmert initd.py)

FILE(GLOB descs "${CMAKE_CURRENT_SOURCE_DIR}/descriptions/*.xml")
INSTALL(FILES ${descs} DESTINATION ${SC3_PACKAGE_APP_DESC_DIR})

FILE(GLOB cfgs "${CMAKE_CURRENT_SOURCE_DIR}/config/*.cfg")
INSTALL(FILES ${cfgs} DESTINATION ${SC3_PACKAGE_CONFIG_DIR})

