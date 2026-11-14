<%@ page contentType="text/html;charset=UTF-8" %>
<!DOCTYPE html>
<html>
<head>
    <title>PowerUp Toolbox Demo View</title>
    <meta name="layout" content="main" />
</head>
<body>
    <div id="contentSection" class="container">
        <h1>訂單管理總覽</h1>

        <g:if test="${flash.message}">
            <div class="alert alert-info">${flash.message}</div>
        </g:if>

        <g:form action="search" method="GET">
            <input type="text" name="keyword" placeholder="搜尋訂單編號..." />
            <button type="submit">查詢</button>
        </g:form>

        <div id="orderTable">
            <g:each in="${orderList}" var="order">
                <div class="order-row">
                    <span>${order.id}</span>
                    <span>${order.customerName}</span>
                    <span>${order.amount}</span>
                </div>
            </g:each>
        </div>
    </div>
</body>
</html>
