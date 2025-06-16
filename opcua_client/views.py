# opcua_client/views.py

from django.shortcuts import render
from datetime import datetime, timezone
from django.http import JsonResponse, HttpResponse
from django.conf import settings
from opcua import Client
from opcua.ua import NodeClass
import json, re, os, time, pytz
from tzlocal import get_localzone
import asyncio
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from concurrent.futures import ThreadPoolExecutor
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
matplotlib.use("Agg")


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
firebase_config_path = os.path.join(BASE_DIR, "serviceAccountKey.json") #substitute with environment variable in production
cred = credentials.Certificate(firebase_config_path)
firebase_admin.initialize_app(
    cred,
    {"storageBucket": "opcuafusion.appspot.com"},
)

stop_events = {}
executor = ThreadPoolExecutor(max_workers=10)


async def start_dashboard(request):

    endpoint = request.GET.get("endpoint", "opc.tcp://0.0.0.0:4840/my_cnc")
    namespaceuri = request.GET.get("namespaceuri", "my_cnc_namespace")
    machine = request.GET.get("machine", "generic_machine")

    try:
        client = Client(endpoint)
        client.connect()
        client.load_type_definitions()
        proceed = True
    except Exception as e:
        proceed = False
        data = {}
        method = {}

        timestamp = datetime.now(timezone.utc)

        db = firestore.client()

        failed_connections_ref = db.collection("failed_connections")

        query = failed_connections_ref.where("endpoint", "==", endpoint).limit(1)
        existing_docs = list(query.stream())

        if existing_docs:
            pass
        else:

            failed_connections_data = {
                "machine": machine,
                "endpoint": endpoint,
                "namespaceuri": namespaceuri,
                "timestamp": timestamp,
                "status": "Failed to connect",
            }

            failed_connections_doc_ref = failed_connections_ref.add(
                failed_connections_data
            )[1]
            failed_connections_id = failed_connections_doc_ref.id

        print(
            "Error in start_dashboard connection: Connection Failed: Server Endpoint Not Set Up"
        )

        context = {
            "error_message": f"Connection Failed: {str(e)}",
            "instruction": "The Endpoint or Namespace URI is incorrect. Please check it again.",
            "endpoint": endpoint,
        }
        return render(request, "opcua_client/connection_error.html", context)

    if proceed:
        try:

            db = firestore.client()

            current_connections_ref = db.collection("current_connections")

            query = current_connections_ref.where("endpoint", "==", endpoint).limit(1)
            existing_docs = list(query.stream())

            if existing_docs:
                context = {
                    "error_message": f"Endpoint already in use in another instance",
                    "instruction": "Please check the endpoint, or close every instance and try again.",
                }
                return render(request, "opcua_client/connection_error.html", context)
            else:
                start_timestamp = datetime.now(timezone.utc)
                current_connections_data = {
                "machine": machine,
                "endpoint": endpoint,
                "namespaceuri": namespaceuri,
                "timestamp": start_timestamp,
            }
            current_connections_doc_ref = current_connections_ref.add(
                current_connections_data
            )[1]
            current_connections_id = current_connections_doc_ref.id

            endpoint_collection_ref = db.collection("endpoint_info")
            endpoint_doc_data = {
                "endpoint": endpoint,
                "last_updated_timestamp": start_timestamp,
            }

            query = endpoint_collection_ref.where("endpoint", "==", endpoint).get()

            if not query:

                endpoint_doc_ref = endpoint_collection_ref.add(endpoint_doc_data)[1]
                current_endpoint_id = endpoint_doc_ref.id
            else:

                for doc in query:
                    current_endpoint_id = doc.id

            timestamp_current = datetime.now(timezone.utc)

            connection_doc_data = {
                "start_timestamp": start_timestamp,
                "stop_timestamp": None,
                "endpoint_id": current_endpoint_id,
                "namespaceuri": namespaceuri,
                "machine": machine,
                "full_node_info_id": None,
                "connection_status": True,
                "last_update_timestamp": timestamp_current,
            }

            connection_collection_ref = db.collection("connection_info")
            connection_doc_ref = connection_collection_ref.add(connection_doc_data)[1]
            connection_info_id = connection_doc_ref.id

        except Exception as e:
            print(
                "Error in start_dashboard function databases either endpoint_info or connection_info:",
                e,
            )

        object_node = client.get_objects_node()
        data = browse_node_names(object_node)
        method = browse_method_nodes(object_node)
        full_node_doc_data = browse_node_full(object_node)

        try:
            full_node_collection_ref = db.collection("full_node_info")
            timestamp_current = datetime.now(timezone.utc)
            full_node_doc_data["last_update_timestamp"] = timestamp_current
            full_node_doc_ref = full_node_collection_ref.add(full_node_doc_data)[1]
            full_node_doc_current_id = full_node_doc_ref.id

            connection_doc_ref = (
                db.collection("connection_info")
                .document(connection_info_id)
                .update(
                    {
                        "full_node_info_id": full_node_doc_current_id,
                        "last_update_timestamp": datetime.now(timezone.utc),
                    }
                )
            )
        except Exception as e:
            print("Error in start_dashboard function databases full_node_info:", e)
        stop_events[connection_info_id] = asyncio.Event()

        stop_events[connection_info_id].clear()
        loop = asyncio.get_event_loop()
        loop.run_in_executor(
            executor,
            asyncio.run,
            database_update_variables(
                object_node, current_endpoint_id, connection_info_id, db, client
            ),
        )

    context = {
        "data": data,
        "endpoint": endpoint,
        "method_context": method,
        "start_timestamp": str(start_timestamp),
        "namespaceuri": namespaceuri,
        "connection_id": {connection_info_id},
    }

    return render(request, "opcua_client/display_dashboard.html", context)


async def database_update_variables(
    node, current_endpoint_id, connection_info_id, db, client
):
    current_stop_event = stop_events[connection_info_id]
    while not current_stop_event.is_set():
        try:
            variables = {}
            variables_from_server = get_variables_with_client_timestamp(node, client)
            variables["endpoint_id"] = current_endpoint_id
            variables["realtime_variables_from_server"] = variables_from_server
            variables["client_timestamp"] = variables[
                "realtime_variables_from_server"
            ].pop("client_timestamp")
            variables["connection_info_id"] = connection_info_id
            database_name = "realtime_variable_" + str(current_endpoint_id)
            realtime_variable_collection_ref = db.collection(database_name)
            variables["last_update_timestamp"] = datetime.now(timezone.utc)
            try:
                if type(
                    variables["realtime_variables_from_server"]["server_timestamp"]
                ) == type(""):
                    variables["server_to_clientUI_in_seconds"] = ""
                else:
                    variables["server_to_clientUI_in_seconds"] = (
                        variables["client_timestamp"]
                        - variables["realtime_variables_from_server"][
                            "server_timestamp"
                        ]
                    ).total_seconds()
            except Exception as e:
                print(
                    "Error in database_update_variable function finding server_timestamp:",
                    e,
                )

            realtime_variable_doc_ref = realtime_variable_collection_ref.add(variables)[
                1
            ]
            realtime_variable_doc_current_id = realtime_variable_doc_ref.id
            await asyncio.sleep(0.0001)
        except Exception as e:
            print("Error in database_update_variable function:", e)

    if current_stop_event.is_set():

        client.disconnect()
        print("Disconnected from the server.")


async def end_connection(request):
    try:

        data = json.loads(request.body)
        endpoint = data.get("endpoint")
        try:
            start_timestamp_str = data.get("start_timestamp")
            start_timestamp = datetime.fromisoformat(start_timestamp_str)
            start_timestamp_utc = start_timestamp.astimezone(timezone.utc)
        except:
            print("error")

        try:

            db = firestore.client()

            connection_collection_ref = db.collection("connection_info")

            endpoint_collection_ref = db.collection("endpoint_info")

            if endpoint_query := endpoint_collection_ref.where(
                "endpoint", "==", endpoint
            ).get():
                for doc in endpoint_query:
                    current_endpoint_id = doc.id

            field_filter1 = FieldFilter("endpoint_id", "==", current_endpoint_id)
            field_filter2 = FieldFilter("connection_status", "==", True)
            field_filter3 = FieldFilter("start_timestamp", "==", start_timestamp_utc)

            query = (
                connection_collection_ref.where(filter=field_filter1)
                .where(filter=field_filter2)
                .where(filter=field_filter3)
                .get()
            )
            # query = connection_collection_ref.where(filter=field_filter1).where(filter=field_filter2).order_by("start_timestamp", direction=firestore.Query.DESCENDING).limit(1).get()

            if query:
                for doc in query:
                    connection_doc_id = doc.id
                db.collection("connection_info").document(connection_doc_id).update(
                    {
                        "connection_status": False,
                        "stop_timestamp": datetime.now(timezone.utc),
                        "last_update_timestamp": datetime.now(timezone.utc),
                    }
                )
            stop_events[connection_doc_id].set()
        except Exception as e:
            print("Error in end_connection function:", e)

        if endpoint:
            return JsonResponse(
                {"end": True, "message": "Connection ended successfully."}
            )
        else:
            return JsonResponse(
                {"end": False, "message": "Failed to end the connection."}
            )

    except Exception as e:

        return JsonResponse({"end": False, "message": str(e)})


def browse_node_names(node):
    node_names = {}

    try:

        node_id = node.nodeid.Identifier
        namespace_index = node.nodeid.NamespaceIndex
        display_name = node.get_display_name().Text

        if display_name != "Server" and display_name != "Aliases":

            node_info = {"node_id": f"ns={namespace_index};i={node_id}"}

            if node.get_node_class() == NodeClass.Object:
                children_data = {}
                for child in node.get_children():
                    child_data = browse_node_names(child)
                    if child_data:

                        child_display_name = child.get_display_name().Text

                        if child_display_name in children_data:
                            child_display_name += f"_{child.nodeid.Identifier}"
                        children_data[child_display_name] = child_data

                node_info["children"] = children_data

            node_names = node_info

    except Exception as e:
        print("Error browse_node_names function browsing opcua server nodes:", e)
    return node_names


def process_method_request(request):
    if request.method == "POST":
        method_name = request.POST.get("method_name")
        input_arguments = {
            key: value
            for key, value in request.POST.items()
            if key.startswith("input_argument")
        }
        custom_description = request.POST.get("identifier_text")
        endpoint = request.POST.get("endpoint")
        namespaceuri = request.POST.get("namespaceuri")
        starttimestamp = request.POST.get("starttimestamp")
        try:
            start_timestamp = datetime.fromisoformat(starttimestamp)
            start_timestamp_utc = start_timestamp.astimezone(timezone.utc)
        except:
            print("error")

        input_argument_list = list(input_arguments.values())
        try:
            client = Client(endpoint, timeout=480)
            client.connect()
            object_node = client.get_objects_node()
            node_names_and_ids = browse_node_names(object_node)
            method_data = browse_method_nodes(object_node)

            input_data_types = [
                each["datatype"] for each in method_data[method_name]["input_arguments"]
            ]

            response_data = {"status": "", "output": "", "message": ""}

            if len(input_data_types) != 0:
                for i in range(len(input_data_types)):
                    if input_data_types[i] == "Float":
                        try:
                            input_argument_list[i] = float(input_argument_list[i])
                        except:
                            response_data = {
                                "status": "Error",
                                "output": "",
                                "message": f"Error in {method_name}. Datatype of the argument invalid",
                            }
                    elif "Int" in input_data_types[i]:
                        try:
                            input_argument_list[i] = int(input_argument_list[i])
                        except:
                            response_data = {
                                "status": "Error",
                                "output": "",
                                "message": f"Error in {method_name}. Datatype of the argument invalid",
                            }
                    elif "Bool" in input_data_types[i]:
                        try:
                            input_argument_list[i] = bool(input_argument_list[i])
                        except:
                            response_data = {
                                "status": "Error",
                                "output": "",
                                "message": f"Error in {method_name}. Datatype of the argument invalid",
                            }
                    elif input_data_types[i] == "String":
                        try:
                            input_argument_list[i] = str(input_argument_list[i]).strip()
                        except:
                            response_data = {
                                "status": "Error",
                                "output": "",
                                "message": f"Error in {method_name}. Datatype of the argument invalid",
                            }

            if response_data["status"] != "Error":

                current_method_parent_node_id = find_parent_node_id(
                    node_names_and_ids, method_name
                )
                namespace_id_pattern = r"ns=(\d+)"
                match = re.search(namespace_id_pattern, current_method_parent_node_id)
                if match:
                    namespace_id = int(match.group(1))
                else:
                    print("Couln't find namespace_id to call the method")

                try:
                    objects = client.get_node(current_method_parent_node_id)
                    if len(input_arguments) > 0:
                        method_return_value = objects.call_method(
                            f"{namespace_id}:{method_name}", *input_argument_list
                        )
                    else:
                        method_return_value = objects.call_method(
                            f"{namespace_id}:{method_name}"
                        )
                except:
                    print(
                        "Error in process_method_request function while calling methods in server:",
                        e,
                    )

                response_data = {
                    "status": "Success",
                    "output": method_return_value,
                    "message": f"Method {method_name} executed successfully",
                }
                client.disconnect()

            try:

                db = firestore.client()

                connection_collection_ref = db.collection("connection_info")

                endpoint_collection_ref = db.collection("endpoint_info")

                if endpoint_query := endpoint_collection_ref.where(
                    "endpoint", "==", endpoint
                ).get():
                    for doc in endpoint_query:
                        current_endpoint_id = doc.id

                field_filter1 = FieldFilter("endpoint_id", "==", current_endpoint_id)
                field_filter2 = FieldFilter("connection_status", "==", True)
                field_filter3 = FieldFilter(
                    "start_timestamp", "==", start_timestamp_utc
                )

                query = (
                    connection_collection_ref.where(filter=field_filter1)
                    .where(filter=field_filter2)
                    .where(filter=field_filter3)
                    .get()
                )
                # query = connection_collection_ref.where(filter=field_filter1).where(filter=field_filter2).order_by("start_timestamp", direction=firestore.Query.DESCENDING).limit(1).get()

                if query:
                    for doc in query:
                        connection_doc_id = doc.id

                last_update_timestamp = datetime.now(timezone.utc)

                if custom_description:
                    instance_method_data = {
                        "endpoint_id": endpoint,
                        "connection_id": connection_doc_id,
                        "input_arguments": input_argument_list,
                        "custom_description": custom_description,
                        "status": response_data["status"],
                        "output": response_data["output"],
                        "output_message": response_data["message"],
                        "last_update_timestamp": last_update_timestamp,
                    }
                else:
                    instance_method_data = {
                        "endpoint_id": endpoint,
                        "connection_id": connection_doc_id,
                        "input_arguments": input_argument_list,
                        "status": response_data["status"],
                        "output": response_data["output"],
                        "output_message": response_data["message"],
                        "last_update_timestamp": last_update_timestamp,
                    }

                instance_method_collection_ref = db.collection("instance_method_info")
                instance_method_doc_ref = instance_method_collection_ref.add(
                    instance_method_data
                )[1]
                instance_method_info_id = instance_method_doc_ref.id

            except Exception as e:
                print("Error in process_method function databases processing:", e)

            return JsonResponse(response_data)
        except Exception as e:
            print(
                "Error in process_method_request function probably connecting to server:",
                e,
            )

    return JsonResponse({"status": "Error", "message": "Invalid request method"})


def get_node_information(request):
    node_id = request.GET.get("node_id", "ns=2;i=1")
    endpoint = request.GET.get("endpoint", "opc.tcp://0.0.0.0:4840/my_cnc")
    node_data = {}

    try:
        client = Client(endpoint)
        client.connect()
        client.load_type_definitions()

        node = client.get_node(node_id)
        node_id = node.nodeid.Identifier
        node_id = int(node_id) if not len(str(node_id)) > 20 else str(node_id)
        namespace_index = node.nodeid.NamespaceIndex
        display_name = node.get_display_name().Text
        node_class = NodeClass(node.get_node_class()).name
        description = node.get_description().Text
        browse_name = node.get_browse_name().Name

        node_data = {
            "identifier": (f"ns={namespace_index};i={node_id}" if node_id else ""),
            "node_class": (node_class if node_class else ""),
            "description": (description if description else ""),
            "browse_name": (browse_name if browse_name else ""),
            "display_name": (display_name if display_name else ""),
            "value_of_variable": "",
            "datatype_of_variable": "",
        }

        if node.get_node_class() == NodeClass.Variable:
            try:
                datatype = node.get_data_type_as_variant_type()
                node_data["datatype_of_variable"] = ((str(datatype)).split("."))[1]
            except:
                node_data["datatype_of_variable"] = ""
            try:
                value_of_variable = node.get_value()
                if node_data["datatype_of_variable"] == "DateTime":
                    node_data["value_of_variable"] = value_of_variable.replace(
                        tzinfo=timezone.utc
                    )
                else:
                    try:

                        value_of_variable = float(value_of_variable)

                        value_of_variable = round(value_of_variable, 2)
                    except ValueError:
                        value_of_variable = node.get_value()
                    node_data["value_of_variable"] = value_of_variable
            except:
                node_data["value_of_variable"] = ""
        client.disconnect()
    except Exception as e:
        print(
            "Error in get_node_information function probably connecting to server or browsing node info:",
            e,
        )

    if len(node_data) == 0:
        node_data["error"] = (
            f"Custom node ID {node_id} for current object in the server; unable to browse"
        )
    return JsonResponse(node_data)


def monitor_realtime_variables(request):
    endpoint = request.GET.get("endpoint", "opc.tcp://0.0.0.0:4840/my_cnc")
    variables_data = {}

    try:
        client = Client(endpoint)
        client.connect()
        client.load_type_definitions()
        proceed = True
    except Exception as e:
        proceed = False
    try:
        if proceed:

            object_node = client.get_objects_node()

            variables_data = get_variables_with_client_timestamp(object_node, client)
            try:
                if type(variables_data["server_timestamp"]) == type(""):
                    variables_data["server_to_clientUI_in_seconds"] = ""
                else:
                    variables_data["server_to_clientUI_in_seconds"] = (
                        variables_data["client_timestamp"]
                        - variables_data["server_timestamp"]
                    ).total_seconds()
            except Exception as e:
                print(
                    "Error in monitor_realtime_variables function while taking difference of server_timestamp and client_timestamp:",
                    e,
                )

            client.disconnect()
    except Exception as e:
        print(
            "Error in monitor_realtime_variables function probably calling browse_node_for variables:",
            e,
        )

    return JsonResponse(variables_data)


def browse_method_nodes(node):

    data_type_map = {
        "1": "Boolean",
        "2": "SByte",
        "3": "Byte",
        "4": "Int16",
        "5": "UInt16",
        "6": "Int32",
        "7": "UInt32",
        "8": "Int64",
        "9": "UInt64",
        "10": "Float",
        "11": "Double",
        "12": "String",
        "13": "DateTime",
        "14": "Guid",
        "15": "ByteString",
        "16": "XmlElement",
        "17": "NodeId",
        "18": "ExpandedNodeId",
        "19": "StatusCode",
        "20": "QualifiedName",
        "21": "LocalizedText",
        "22": "DataValue",
        "23": "Variant",
        "24": "DiagnosticInfo",
        "25": "Number",
        "26": "Integer",
        "27": "UInteger",
        "28": "Enumeration",
        "29": "Structure",
        "30": "BaseDataType",
    }

    method_data = {}

    try:
        display_name = node.get_display_name().Text
        if display_name != "Server" and display_name != "Aliases":

            node_id = node.nodeid.Identifier
            namespace_index = node.nodeid.NamespaceIndex
            node_class = node.get_node_class()
            description = node.get_description().Text
            browse_name = node.get_browse_name().Name

            if node_class == NodeClass.Method:

                node_info = {
                    "node_id": f"ns={namespace_index};i={node_id}",
                    "identifier": int(node_id),
                    "node_class": NodeClass(node_class).name,
                    "description": description,
                    "browse_name": browse_name,
                    "display_name": display_name,
                }

                number_of_arguments = len(node.get_children())
                node_info["number_of_arguments"] = number_of_arguments

                input_arguments = []
                output_arguments = []

                for argument_index in range(number_of_arguments):
                    child = node.get_children()[argument_index]
                    child_name = child.get_browse_name().Name

                    if child_name == "InputArguments":
                        try:
                            input_arguments_node = child
                            input_arguments_list = input_arguments_node.get_value()

                            for input_argument in input_arguments_list:
                                input_arguments.append(
                                    {
                                        "name": input_argument.Name,
                                        "datatype": data_type_map.get(
                                            f"{input_argument.DataType.Identifier}",
                                            "Unknown",
                                        ),
                                        "description": input_argument.Description.Text,
                                    }
                                )

                        except Exception as e:
                            print("Error retrieving input arguments:", e)

                    elif child_name == "OutputArguments":
                        try:
                            output_arguments_node = child
                            output_arguments_list = output_arguments_node.get_value()

                            for output_argument in output_arguments_list:
                                output_arguments.append(
                                    {
                                        "name": output_argument.Name,
                                        "datatype": data_type_map.get(
                                            f"{output_argument.DataType.Identifier}",
                                            "Unknown",
                                        ),
                                        "description": output_argument.Description.Text,
                                    }
                                )

                        except Exception as e:
                            print("Error retrieving output arguments:", e)

                node_info["input_arguments"] = input_arguments
                node_info["output_arguments"] = output_arguments

                method_data[display_name] = node_info

            for child in node.get_children():
                child_method_data = browse_method_nodes(child)
                method_data.update(child_method_data)

    except Exception as e:
        print("Error in browse_method_nodes function:", e)

    return method_data


def get_variables_with_client_timestamp(node, client):
    variables = browse_node_for_variables_from_server(node, client)
    try:
        for key, value in variables.items():
            if isinstance(value, datetime):

                variables["server_timestamp"] = value
                break
        if not variables["server_timestamp"]:
            variables["server_timestamp"] = ""
    except:
        variables["server_timestamp"] = ""
    client_timestamp = datetime.now(timezone.utc)
    variables["client_timestamp"] = client_timestamp
    return variables


def browse_node_for_variables_from_server(node, client):
    """
    Recursive function to explore an OPC UA node and its children, specifically identifying
    variable nodes and retrieving their names and values.
    """
    variables = {}

    try:
        display_name = node.get_display_name().Text
        if display_name != "Server":

            node_class = node.get_node_class()
            browse_name = node.get_browse_name().Name
            description = node.get_description().Text

            if (
                not "propert" in browse_name
                or not "propert" in display_name
                or not "propert" in description
            ):
                if node_class == NodeClass.Variable:
                    try:
                        datatype = node.get_data_type_as_variant_type()
                        datatype_of_variable = ((str(datatype)).split("."))[1]
                    except:
                        datatype_of_variable = ""
                    try:
                        value_of_variable = node.get_value()
                        if datatype_of_variable == "DateTime":
                            value_of_variable = value_of_variable.replace(
                                tzinfo=timezone.utc
                            )
                        else:
                            try:

                                value_of_variable = float(value_of_variable)

                                value_of_variable = round(value_of_variable, 2)
                            except ValueError:
                                value_of_variable = node.get_value()
                            value_of_variable = value_of_variable
                    except:
                        value_of_variable = ""

                    variables[("_").join(display_name.split(" "))] = value_of_variable
                if node_class == NodeClass.Object:

                    for child in node.get_children():
                        child_variables = browse_node_for_variables_from_server(
                            child, client
                        )
                        if child_variables:
                            variables.update(child_variables)

    except Exception as e:
        print("Error in get_variables_with_client_timestamp function:", e)
        try:
            client.connect()
            client.load_type_definitions()
            object_node = client.get_objects_node()
            browse_node_for_variables_from_server(object_node, client)

        except:
            pass

    return variables


def find_parent_node_id(data, target_key):
    if isinstance(data, dict):
        for key, value in data.items():
            if key == "children" and isinstance(value, dict):
                if target_key in value:
                    return data["node_id"]
                for child_key in value:
                    result = find_parent_node_id(value[child_key], target_key)
                    if result:
                        return result
            elif isinstance(value, dict):
                result = find_parent_node_id(value, target_key)
                if result:
                    return result
    return None


def browse_node_full(node):
    """
    Recursive function to explore an OPC UA node and its children. Returns a hierarchical dictionary
    where keys are the display names of the nodes, containing detailed information about each node.
    """

    data_type_map = {
        "1": "Boolean",
        "2": "SByte",
        "3": "Byte",
        "4": "Int16",
        "5": "UInt16",
        "6": "Int32",
        "7": "UInt32",
        "8": "Int64",
        "9": "UInt64",
        "10": "Float",
        "11": "Double",
        "12": "String",
        "13": "DateTime",
        "14": "Guid",
        "15": "ByteString",
        "16": "XmlElement",
        "17": "NodeId",
        "18": "ExpandedNodeId",
        "19": "StatusCode",
        "20": "QualifiedName",
        "21": "LocalizedText",
        "22": "DataValue",
        "23": "Variant",
        "24": "DiagnosticInfo",
        "25": "Number",
        "26": "Integer",
        "27": "UInteger",
        "28": "Enumeration",
        "29": "Structure",
        "30": "BaseDataType",
    }

    node_data_dict = {}

    try:

        node_id = node.nodeid.Identifier
        namespace_index = node.nodeid.NamespaceIndex
        display_name = node.get_display_name().Text
        node_class = node.get_node_class()
        description = node.get_description().Text
        browse_name = node.get_browse_name().Name

        # Skip the "Server" node for data extraction
        if display_name != "Server":
            # Construct a dictionary for the current node's detailed information
            node_info = {
                "node_id": f"ns={namespace_index};i={node_id}",
                "identifier": (
                    int(node_id) if not len(str(node_id)) > 20 else str(node_id)
                ),
                "node_class": NodeClass(node_class).name,
                "description": description,
                "browse_name": browse_name,
                "display_name": display_name,
            }

            if node_class == NodeClass.Variable:
                try:
                    datatype = node.get_data_type_as_variant_type()
                    node_info["datatype_of_variable"] = ((str(datatype)).split("."))[1]
                except:
                    node_info["datatype_of_variable"] = ""
                try:
                    value_of_variable = node.get_value()
                    if node_info["value_of_variable"] == "DateTime":
                        node_info["value_of_variable"] = value_of_variable.replace(
                            tzinfo=timezone.utc
                        )
                    else:
                        try:

                            value_of_variable = float(value_of_variable)

                            value_of_variable = round(value_of_variable, 2)
                        except ValueError:
                            value_of_variable = node.get_value()
                        node_info["value_of_variable"] = value_of_variable
                except:
                    node_info["value_of_variable"] = ""

            elif node_class == NodeClass.Method:
                number_of_arguments = len(node.get_children())
                node_info["number_of_arguments"] = number_of_arguments

                input_arguments = []
                output_arguments = []

                for argument_index in range(number_of_arguments):
                    child = node.get_children()[argument_index]
                    child_name = child.get_browse_name().Name

                    if child_name == "InputArguments":
                        try:
                            input_arguments_node = child
                            input_arguments_list = input_arguments_node.get_value()

                            for input_argument in input_arguments_list:
                                input_arguments.append(
                                    {
                                        "name": input_argument.Name,
                                        "datatype": data_type_map.get(
                                            f"{input_argument.DataType.Identifier}",
                                            "Unknown",
                                        ),
                                        "description": input_argument.Description.Text,
                                    }
                                )

                        except Exception as e:
                            print("Error retrieving input arguments:", e)

                    elif child_name == "OutputArguments":
                        try:
                            output_arguments_node = child
                            output_arguments_list = output_arguments_node.get_value()

                            for output_argument in output_arguments_list:
                                output_arguments.append(
                                    {
                                        "name": output_argument.Name,
                                        "datatype": data_type_map.get(
                                            f"{output_argument.DataType.Identifier}",
                                            "Unknown",
                                        ),
                                        "description": output_argument.Description.Text,
                                    }
                                )

                        except Exception as e:
                            print("Error retrieving output arguments:", e)

                node_info["input_arguments"] = input_arguments
                node_info["output_arguments"] = output_arguments

            if node_class == NodeClass.Object:
                children_data = {}
                for child in node.get_children():
                    child_data = browse_node_full(child)
                    if child_data:

                        child_display_name = ("_").join(
                            child.get_display_name().Text.split(" ")
                        )

                        if child_display_name in children_data:
                            child_display_name += f"_{child.nodeid.Identifier}"
                        children_data[child_display_name] = child_data

                node_info["children"] = children_data

            node_data_dict = node_info

    except Exception as e:
        print("Error in browse_node_full function:", e)

    return node_data_dict


def generate_graph(request):

    image_assets_path = os.path.join(
        settings.BASE_DIR, "opcua_client", "static", "opcua_client", "image_assets"
    )
    image_urls = []

    if request.method == "GET":

        image_files = [f for f in os.listdir(image_assets_path) if f.endswith(".png")]
        for image_file in image_files:
            image_urls.append(f"opcua_client/image_assets/{image_file}")

        endpoint = request.GET.get("endpoint", "opc.tcp://0.0.0.0:4840/my_cnc")
        namespaceuri = request.GET.get("namespaceuri", "my_cnc")
        variables_data = {}

        try:
            client = Client(endpoint)
            client.connect()
            client.load_type_definitions()
            proceed = True
        except Exception as e:
            print(f"Connection error: {e}")
            proceed = False

        if proceed:
            try:
                object_node = client.get_objects_node()
                variables_data = get_variables_with_client_timestamp(
                    object_node, client
                )

                try:
                    if isinstance(variables_data.get("server_timestamp"), str):
                        variables_data["server_to_clientUI_in_seconds"] = ""
                    else:
                        server_time = variables_data.get("server_timestamp")
                        client_time = variables_data.get("client_timestamp")
                        if server_time and client_time:
                            variables_data["server_to_clientUI_in_seconds"] = (
                                client_time - server_time
                            ).total_seconds()
                        else:
                            variables_data["server_to_clientUI_in_seconds"] = ""
                except Exception as e:
                    print(f"Error computing server-to-client time difference: {e}")

                client.disconnect()

            except Exception as e:
                print(f"Error fetching variables from OPC UA server: {e}")

        print(endpoint, image_urls)

        return render(
            request,
            "opcua_client/generate_graph.html",
            {
                "variables_data": variables_data,
                "image_urls": image_urls,
                "endpoint": endpoint,
                "namespaceuri": namespaceuri,
            },
        )

    if request.method == "POST":
        try:
            data = json.loads(request.body)
            x_axis_variables = data.get("x_axis_variables", [])
            y_axis_variables = data.get("y_axis_variables", [])
            from_date_str = data.get("from_date")
            to_date_str = data.get("to_date")
            endpoint = data.get("endpoint")

            from_date = datetime.fromisoformat(from_date_str) if from_date_str else None
            to_date = datetime.fromisoformat(to_date_str) if to_date_str else None

            db = firestore.client()
            endpoint_info_ref = db.collection("endpoint_info")
            query = endpoint_info_ref.where("endpoint", "==", endpoint).limit(1)
            endpoint_info_docs = list(query.stream())

            if not endpoint_info_docs:
                return JsonResponse(
                    {"status": "error", "message": "Endpoint not found."}, status=404
                )

            endpoint_info_doc = endpoint_info_docs[0]
            endpoint_info_id = endpoint_info_doc.id
            realtime_collection_name = f"realtime_variable_{endpoint_info_id}"
            realtime_collection_ref = db.collection(realtime_collection_name)

            query = realtime_collection_ref.where(
                "last_update_timestamp", ">=", from_date
            ).where("last_update_timestamp", "<=", to_date)
            filtered_docs = list(query.stream())

            if not filtered_docs:
                return JsonResponse(
                    {
                        "status": "error",
                        "message": "No data found for the selected date range.",
                    },
                    status=404,
                )

            filtered_data = []
            for doc in filtered_docs:
                doc_data = doc.to_dict()
                doc_data.pop("connection_info_id", None)
                doc_data.pop("endpoint_id", None)
                doc_data.pop("last_update_timestamp", None)

                if "realtime_variables_from_server" in doc_data:
                    realtime_variables = doc_data.pop(
                        "realtime_variables_from_server", {}
                    )
                    doc_data.update(realtime_variables)

                filtered_data.append(doc_data)

            namespaceuri = re.search(r"[^/]+$", endpoint).group()
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            image_name = f"{namespaceuri}_graph_{timestamp}.png"
            image_path = os.path.join(image_assets_path, image_name)

            dynamic_plot(filtered_data, x_axis_variables, y_axis_variables, image_path)

            image_urls.append(f"opcua_client/image_assets/{image_name}")
            return JsonResponse({"image_urls": image_urls}, status=200)
        
        except ValueError as ve:
            return JsonResponse({"status": "error", "message": str(ve)}, status=400)
        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=500)


def dynamic_plot(data, x_keys, y_keys, save_path):

    try:
        plt.figure(figsize=(10, 6))
    except Exception as e:
        print(f"Error creating figure: {e}")
        return

    try:
        for x_key in x_keys:
            try:
                x_data = [entry[x_key] for entry in data]
            except KeyError as e:
                print(f"KeyError extracting x_data for {x_key}: {e}")
                continue

            for y_key in y_keys:
                try:
                    y_data = [entry[y_key] for entry in data]
                except KeyError as e:
                    print(f"KeyError extracting y_data for {y_key}: {e}")
                    continue

                try:
                    plt.plot(x_data, y_data, label=f"{y_key} vs {x_key}", marker="o")
                except Exception as e:
                    print(f"Error plotting {y_key} vs {x_key}: {e}")

            if isinstance(x_data[0], datetime):
                try:
                    plt.gca().xaxis.set_major_formatter(
                        mdates.DateFormatter("%Y-%m-%d %H:%M:%S", tz=get_localzone())
                    )
                    plt.gca().xaxis.set_major_locator(mdates.SecondLocator())
                    plt.gcf().autofmt_xdate()
                except Exception as e:
                    print(f"Error formatting datetime axis: {e}")

    except Exception as e:
        print(f"Error during plotting: {e}")
        return

    try:
        plt.xlabel("X Values")
        plt.ylabel("Y Values")
        plt.title(f"{', '.join(y_keys)} vs {', '.join(x_keys)}")
        plt.legend()
    except Exception as e:
        print(f"Error setting labels, title, or legend: {e}")

    try:
        plt.savefig(save_path)
    except Exception as e:
        print(f"Error saving figure to {save_path}: {e}")
    finally:
        plt.close()


def convert_to_local_time(data):

    local_timezone = get_localzone()
    for record in data:

        utc_server_timestamp = record["server_timestamp"]
        utc_client_timestamp = record["client_timestamp"]

        local_server_timestamp = utc_server_timestamp.astimezone(local_timezone)
        local_client_timestamp = utc_client_timestamp.astimezone(local_timezone)

        record["server_timestamp"] = local_server_timestamp
        record["client_timestamp"] = local_client_timestamp

    return data


def delete_graph(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            image_name = data.get("image_name")
            image_path = os.path.join(
                settings.BASE_DIR,
                "opcua_client",
                "static",
                "opcua_client",
                "image_assets",
                image_name,
            )

            if os.path.exists(image_path):
                os.remove(image_path)
                return JsonResponse({"status": "success"})
            else:
                return JsonResponse(
                    {"status": "error", "message": "File not found."}, status=404
                )
        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=500)


def download_data(request):
    if request.method == "GET":
        endpoint = request.GET.get("endpoint", "opc.tcp://0.0.0.0:4840/my_cnc")
        namespaceuri = request.GET.get("namespaceuri", "my_cnc")
        return render(
            request,
            "opcua_client/download_data.html",
            {"endpoint": endpoint, "namespaceuri": namespaceuri},
        )

    if request.method == "POST":
        try:
            data = json.loads(request.body)
            from_date_str = data.get("from_date")
            to_date_str = data.get("to_date")
            endpoint = data.get("endpoint")

            from_date = datetime.fromisoformat(from_date_str) if from_date_str else None
            to_date = datetime.fromisoformat(to_date_str) if to_date_str else None

            if not from_date or not to_date:
                return JsonResponse(
                    {"status": "error", "message": "Invalid date range."}, status=400
                )

            db = firestore.client()
            endpoint_info_ref = db.collection("endpoint_info")
            query = endpoint_info_ref.where("endpoint", "==", endpoint).limit(1)
            endpoint_info_docs = list(query.stream())

            if not endpoint_info_docs:
                return JsonResponse(
                    {"status": "error", "message": "Endpoint not found."}, status=404
                )

            endpoint_info_doc = endpoint_info_docs[0]
            endpoint_info_id = endpoint_info_doc.id
            realtime_collection_name = f"realtime_variable_{endpoint_info_id}"
            realtime_collection_ref = db.collection(realtime_collection_name)

            query = realtime_collection_ref.where(
                "last_update_timestamp", ">=", from_date
            ).where("last_update_timestamp", "<=", to_date)
            filtered_docs = list(query.stream())

            if not filtered_docs:
                return JsonResponse(
                    {
                        "status": "error",
                        "message": "No data found for the selected date range.",
                    },
                    status=404,
                )

            filtered_data = []
            for doc in filtered_docs:
                doc_data = doc.to_dict()
                doc_data.pop("connection_info_id", None)
                doc_data.pop("endpoint_id", None)

                if "realtime_variables_from_server" in doc_data:
                    realtime_variables = doc_data.pop(
                        "realtime_variables_from_server", {}
                    )
                    doc_data.update(realtime_variables)

                filtered_data.append(doc_data)

            df = pd.DataFrame(filtered_data)

            response = HttpResponse(content_type="text/csv")
            response["Content-Disposition"] = (
                f'attachment; filename="data_{endpoint_info_id}_{from_date_str}_{to_date_str}.csv"'
            )

            df.to_csv(path_or_buf=response, index=False)

            return response

        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=500)
